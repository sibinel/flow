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

function mediaLabel(kind) {
  const names = {
    imageMessage: 'imagem',
    documentMessage: 'documento',
    videoMessage: 'vídeo',
    ptvMessage: 'vídeo',
    audioMessage: 'áudio',
    stickerMessage: 'figurinha',
  }
  return names[kind] || ''
}

const DOWNLOADABLE = new Set([
  'imageMessage',
  'documentMessage',
  'videoMessage',
  'ptvMessage',
  'audioMessage',
  'stickerMessage',
])

const FILE_NAMES = {
  imageMessage: 'imagem.jpg',
  documentMessage: 'anexo',
  videoMessage: 'video.mp4',
  ptvMessage: 'video.mp4',
  audioMessage: 'audio.ogg',
  stickerMessage: 'figurinha.webp',
}

function cursorFile(folder) {
  return path.join(folder, 'cursor.json')
}

function loadCursor(folder) {
  try {
    const data = JSON.parse(readFileSync(cursorFile(folder), 'utf8'))
    return data && typeof data === 'object' ? data : {}
  } catch {
    return {}
  }
}

function fetchHistoryPage(sock, key, seconds, timeout) {
  return new Promise((resolve, reject) => {
    let requestId
    let done = false
    const pending = new Map()
    const finish = (error, messages) => {
      if (done) return
      done = true
      clearTimeout(timer)
      sock.ev.off('messaging-history.set', onHistory)
      if (error) reject(error)
      else resolve(messages)
    }
    const onHistory = (event) => {
      const id = event.peerDataRequestSessionId
      if (!id) return
      if (id === requestId) finish(null, event.messages || [])
      else if (!requestId) pending.set(id, event.messages || [])
    }
    const timer = setTimeout(() => finish(new Error('tempo esgotado')), timeout)
    sock.ev.on('messaging-history.set', onHistory)
    Promise.resolve().then(() => sock.fetchMessageHistory(50, key, seconds)).then((id) => {
      if (done) return
      if (!id) return finish(new Error('ID da solicitação indisponível'))
      requestId = id
      if (pending.has(id)) finish(null, pending.get(id))
    }, (error) => finish(error))
  })
}

async function askGap(sock, cursor, overlap, warnings) {
  const entries = Object.entries(cursor)
  if (!entries.length) {
    warnings.push('Histórico inicial pode estar incompleto; a sincronização não garante todas as mensagens.')
    return
  }
  if (typeof sock.fetchMessageHistory !== 'function' || typeof sock.ev.off !== 'function') {
    warnings.push('Histórico sob demanda indisponível; mensagens anteriores podem estar faltando.')
    return
  }
  const deadline = Date.now() + 8000
  for (const [chat, mark] of entries) {
    const digits = String(chat).replace(/\D/g, '')
    const time = Date.parse(mark?.time)
    if (!digits || !mark?.id || !Number.isFinite(time)) {
      warnings.push(`Cursor inválido para ${chat}; histórico não solicitado.`)
      continue
    }
    const boundary = time / 1000 - overlap
    let seconds = Math.floor(time / 1000)
    let key = { remoteJid: `${digits}@s.whatsapp.net`, id: String(mark.id), fromMe: false }
    let covered = false
    try {
      for (let page = 0; page < 10; page += 1) {
        const remaining = deadline - Date.now()
        if (remaining <= 0) throw new Error('tempo total esgotado')
        const messages = await fetchHistoryPage(sock, key, seconds, Math.min(remaining, 3000))
        const candidates = messages.filter((msg) => phoneOf(msg) === `+${digits}` && msg.key?.id)
          .map((msg) => ({ msg, time: Number(msg.messageTimestamp?.toString?.() ?? msg.messageTimestamp) }))
          .filter((item) => Number.isFinite(item.time) && item.time > 0)
          .sort((a, b) => a.time - b.time)
        const oldest = candidates[0]
        if (!oldest) throw new Error('página sem mensagens utilizáveis')
        if (oldest.time <= boundary) {
          covered = true
          break
        }
        if (oldest.time >= seconds) throw new Error('paginação sem progresso')
        key = oldest.msg.key
        seconds = oldest.time
      }
      if (!covered) warnings.push(`Histórico de ${chat} pode estar incompleto: limite de páginas atingido.`)
    } catch (error) {
      warnings.push(`Histórico de ${chat} pode estar incompleto: ${error?.message || error}.`)
    }
    warnings.push(`Histórico de ${chat} não garante recuperação completa; a chave inicial do cursor é aproximada.`)
  }
}

function applyCursor(cursor, messages, overlap) {
  const byChat = new Map()
  for (const message of messages) {
    const list = byChat.get(message.from) || []
    list.push(message)
    byChat.set(message.from, list)
  }
  const kept = []
  for (const [chat, list] of byChat) {
    list.sort((a, b) => String(a.received_at).localeCompare(String(b.received_at)))
    const mark = cursor[chat]
    for (const message of list) {
      const time = Date.parse(message.received_at)
      const markTime = Date.parse(mark?.time)
      const after = !Number.isFinite(markTime) || !Number.isFinite(time) || time >= markTime - overlap * 1000
      if (after) kept.push(message)
    }
  }
  return kept
}

async function attachmentOf(msg, sock) {
  const content = extractMessageContent(msg.message) || {}
  const kind = getContentType(content)
  if (!kind || kind === 'conversation' || kind === 'extendedTextMessage') {
    return { file: null, note: '' }
  }
  const label = mediaLabel(kind) || kind
  if (!DOWNLOADABLE.has(kind)) {
    return { file: null, note: '' }
  }
  const media = content[kind] || {}
  try {
    const buffer = await downloadMediaMessage(
      msg,
      'buffer',
      {},
      { logger, reuploadRequest: (item) => sock.updateMediaMessage(item) },
    )
    if (!buffer || buffer.length > 15 * 1024 * 1024) {
      const why = buffer && buffer.length > 15 * 1024 * 1024 ? ' acima de 15 MB' : ''
      return { file: null, note: `mídia não baixada: ${label}${why}` }
    }
    const name = media.fileName || FILE_NAMES[kind] || 'anexo'
    return {
      file: { name: path.basename(String(name)), data_b64: Buffer.from(buffer).toString('base64') },
      note: '',
    }
  } catch {
    return { file: null, note: `mídia não baixada: ${label}` }
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
  const cursor = loadCursor(folder)
  const warnings = []
  let overlap = request.overlap_seconds ?? 604800
  if (typeof overlap !== 'number' || !Number.isFinite(overlap) || overlap < 0) {
    warnings.push('overlap_seconds inválido; usado o padrão de 604800 segundos.')
    overlap = 604800
  }
  const sock = await connect(folder, false, (socket) => {
    const take = (list) => {
      for (const msg of list || []) {
        const id = msg?.key?.id
        const chat = phoneOf(msg)
        if (!id || !chat || msg.key.fromMe) continue
        const key = JSON.stringify([chat, id])
        if (!collected.has(key)) collected.set(key, msg)
      }
    }
    socket.ev.on('messaging-history.set', ({ messages }) => take(messages))
    socket.ev.on('messages.upsert', ({ messages }) => take(messages))
  })
  await askGap(sock, cursor, overlap, warnings)
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
    const receivedAt = isoTime(msg)
    if (!applyCursor(cursor, [{ from, received_at: receivedAt }], overlap).length) continue
    const text = textOf(msg)
    const found = await attachmentOf(msg, sock)
    if (found.note) warnings.push(`${from} (${msg.key.id}): ${found.note}`)
    messages.push({
      id: msg.key.id,
      from,
      text,
      received_at: receivedAt,
      attachments: found.file ? [found.file] : [],
      media_note: found.note,
    })
  }
  end(sock)
  return { messages: applyCursor(cursor, messages, overlap), warnings }
}

// Mesmo critério de flow/incoming.py: celular brasileiro com e sem o nono dígito.
function phoneVariants(digits) {
  const found = [digits]
  if (digits.startsWith('55') && digits[2] !== '0' && digits[3] !== '0') {
    if (digits.length === 13 && digits[4] === '9' && '6789'.includes(digits[5])) {
      found.push(digits.slice(0, 4) + digits.slice(5))
    } else if (digits.length === 12 && '6789'.includes(digits[4])) {
      found.push(digits.slice(0, 4) + '9' + digits.slice(4))
    }
  }
  return found
}

async function resolveJid(sock, digits) {
  for (const candidate of phoneVariants(digits)) {
    const found = await sock.onWhatsApp(`${candidate}@s.whatsapp.net`)
    const hit = (found || []).find((item) => item && item.exists && item.jid)
    if (hit) return hit.jid
  }
  return null
}

async function sendMessage(folder) {
  const digits = String(request.to || '').replace(/\D/g, '')
  if (!digits) return { sent: false, handed: false }
  let sock
  try {
    sock = await connect(folder, false, null)
  } catch (error) {
    if (error && error.code === 'qr') throw error
    return { sent: false, handed: false }
  }
  let jid
  try {
    jid = await resolveJid(sock, digits)
  } catch {
    end(sock)
    return { sent: false, handed: false }
  }
  if (!jid) {
    end(sock)
    return { sent: false, handed: false, error: 'not_on_whatsapp' }
  }
  try {
    await sock.sendMessage(jid, { text: String(request.text || '') })
  } catch {
    end(sock)
    return { sent: false, handed: true }
  }
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
