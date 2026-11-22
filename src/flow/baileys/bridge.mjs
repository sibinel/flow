/**
 * Ponte da sessão Web. Conta pessoal, protocolo do WhatsApp Web, sem navegador.
 * Sem credenciais registradas, leitura e envio saem antes de abrir a conexão.
 */
import { existsSync, readFileSync, unlinkSync } from 'node:fs'
import path from 'node:path'
import pino from 'pino'
import QRCode from 'qrcode'
import makeWASocket, {
  Browsers,
  DisconnectReason,
  downloadMediaMessage,
  extractMessageContent,
  getContentType,
  isJidUser,
  jidNormalizedUser,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys'

const logger = pino({ level: 'silent' }, pino.destination(2))
const request = JSON.parse(readFileSync(0, 'utf8'))
const op = request.op
const session = path.resolve(String(request.session || ''))

function fail(error) {
  if (error && error.code === 'qr') {
    process.stdout.write(JSON.stringify({ error: 'qr' }))
    process.exit(2)
  }
  process.stderr.write(`${error && error.message ? error.message : error}\n`)
  process.exit(1)
}

function assertSessionPath(folder) {
  const parts = folder.split(path.sep).map((part) => part.toLowerCase())
  if (parts.includes('documentos')) {
    throw new Error('a sessão do WhatsApp não pode ficar na pasta de documentos')
  }
}

function registered(folder) {
  const file = path.join(folder, 'creds.json')
  if (!existsSync(file)) return false
  try {
    return JSON.parse(readFileSync(file, 'utf8')).registered === true
  } catch {
    return false
  }
}

function qrNeeded() {
  const error = new Error('qr')
  error.code = 'qr'
  return error
}

function end(sock) {
  try {
    sock.end(undefined)
  } catch {
    /* a ponte termina de todo jeito */
  }
}

function clearQr(folder) {
  try {
    unlinkSync(path.join(folder, 'qr.png'))
  } catch {
    /* ainda não havia QR */
  }
}

async function showQr(folder, qr, gate) {
  const drawn = await QRCode.toString(qr, { type: 'terminal', small: true })
  process.stderr.write(drawn.endsWith('\n') ? drawn : `${drawn}\n`)
  if (gate.open) return
  await QRCode.toFile(path.join(folder, 'qr.png'), qr)
  if (gate.open) clearQr(folder)
}

function waitConnection(sock, folder, allowQr) {
  const gate = { open: false }
  return new Promise((resolve, reject) => {
    let done = false
    const finish = (value) => {
      if (done) return
      done = true
      if (value === 'open') gate.open = true
      clearTimeout(timer)
      resolve(value)
    }
    const timer = setTimeout(() => {
      if (done) return
      done = true
      reject(new Error('tempo esgotado'))
    }, allowQr ? 180000 : 30000)
    sock.ev.on('connection.update', (update) => {
      if (update.qr) {
        if (!allowQr) {
          end(sock)
          finish('qr')
          return
        }
        showQr(folder, update.qr, gate).catch((error) => {
          if (done) return
          done = true
          clearTimeout(timer)
          reject(error)
        })
      }
      if (update.connection === 'open') finish('open')
      if (update.connection === 'close') {
        const code = update.lastDisconnect?.error?.output?.statusCode
        if (code === DisconnectReason.restartRequired) finish('restart')
        else if (code === DisconnectReason.loggedOut) finish('loggedOut')
        else finish('close')
      }
    })
  })
}

async function connect(folder, allowQr, onSocket) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const { state, saveCreds } = await useMultiFileAuthState(folder)
    if (!allowQr && state.creds.registered !== true) throw qrNeeded()
    const sock = makeWASocket({
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      logger,
      browser: Browsers.ubuntu('Flow'),
      markOnlineOnConnect: false,
      syncFullHistory: false,
    })
    sock.ev.on('creds.update', saveCreds)
    if (onSocket) onSocket(sock)
    const outcome = await waitConnection(sock, folder, allowQr)
    if (outcome === 'open') {
      clearQr(folder)
      return sock
    }
    end(sock)
    if (outcome === 'qr' || outcome === 'loggedOut') throw qrNeeded()
    if (outcome !== 'restart') throw new Error('conexão fechada')
  }
  throw new Error('não conectou')
}

function phoneOf(msg) {
  const candidates = [msg.key?.senderPn, msg.key?.remoteJidAlt, msg.key?.remoteJid]
  for (const jid of candidates) {
    if (!jid || !isJidUser(jid)) continue
    const user = jidNormalizedUser(jid).split('@')[0].split(':')[0].replace(/\D/g, '')
    if (user) return `+${user}`
  }
  return ''
}

function textOf(msg) {
  const content = extractMessageContent(msg.message) || {}
  return (
    content.conversation ||
    content.extendedTextMessage?.text ||
    content.imageMessage?.caption ||
    content.documentMessage?.caption ||
    content.videoMessage?.caption ||
    ''
  )
}

async function attachmentOf(msg, sock) {
  const content = extractMessageContent(msg.message) || {}
  const kind = getContentType(content)
  if (kind !== 'imageMessage' && kind !== 'documentMessage') return null
  const media = content[kind] || {}
  try {
    const buffer = await downloadMediaMessage(
      msg,
      'buffer',
      {},
      { logger, reuploadRequest: (item) => sock.updateMediaMessage(item) },
    )
    if (!buffer || buffer.length > 15 * 1024 * 1024) return null
    const name = media.fileName || (kind === 'imageMessage' ? 'imagem.jpg' : 'anexo')
    return { name: path.basename(String(name)), data_b64: Buffer.from(buffer).toString('base64') }
  } catch {
    return null
  }
}

function isoTime(msg) {
  const raw = msg.messageTimestamp
  const seconds = Number(raw?.toString?.() ?? raw ?? 0)
  if (!Number.isFinite(seconds) || seconds <= 0) return new Date().toISOString()
  return new Date(seconds * 1000).toISOString()
}

async function readMessages(folder) {
  const collected = new Map()
  const sock = await connect(folder, false, (socket) => {
    const take = (list) => {
      for (const msg of list || []) {
        const id = msg?.key?.id
        if (!id || msg.key.fromMe || collected.has(id)) continue
        collected.set(id, msg)
      }
    }
    socket.ev.on('messaging-history.set', ({ messages }) => take(messages))
    socket.ev.on('messages.upsert', ({ messages }) => take(messages))
  })
  await new Promise((resolve) => {
    const timer = setTimeout(resolve, 8000)
    sock.ev.on('messaging-history.set', ({ isLatest }) => {
      if (!isLatest) return
      clearTimeout(timer)
      setTimeout(resolve, 300)
    })
  })
  const messages = []
  for (const msg of collected.values()) {
    const from = phoneOf(msg)
    if (!from) continue
    const text = textOf(msg)
    const attachment = await attachmentOf(msg, sock)
    if (!text && !attachment) continue
    messages.push({
      from,
      text,
      received_at: isoTime(msg),
      attachments: attachment ? [attachment] : [],
    })
  }
  end(sock)
  return { messages }
}

async function sendMessage(folder) {
  const digits = String(request.to || '').replace(/\D/g, '')
  if (!digits) throw new Error('destino vazio')
  const sock = await connect(folder, false, null)
  await sock.sendMessage(`${digits}@s.whatsapp.net`, { text: String(request.text || '') })
  await new Promise((resolve) => setTimeout(resolve, 300))
  end(sock)
  return { sent: true }
}

async function pairDevice(folder) {
  const sock = await connect(folder, true, null)
  await new Promise((resolve) => setTimeout(resolve, 300))
  end(sock)
  return { ready: true }
}

try {
  assertSessionPath(session)
  if (op !== 'pair' && !registered(session)) throw qrNeeded()
  const result = op === 'pair'
    ? await pairDevice(session)
    : op === 'read'
      ? await readMessages(session)
      : op === 'send'
        ? await sendMessage(session)
        : null
  if (result === null) throw new Error('operação desconhecida')
  process.stdout.write(JSON.stringify(result))
  process.exit(0)
} catch (error) {
  fail(error)
}
