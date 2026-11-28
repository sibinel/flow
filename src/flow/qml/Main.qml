import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    visible: true
    width: 1080
    height: 720
    minimumWidth: 960
    minimumHeight: 640
    title: "flow"
    color: "#141210"

    property int selectedEntry: -1
    property bool selectedUnowned: false

    function processChoice() {
        if (processes.currentIndex < 0 || processes.currentValue === undefined)
            return 0
        return processes.currentValue
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 32
        spacing: 24

        ColumnLayout {
            spacing: 6
            Layout.fillWidth: true

            Label {
                text: "flow"
                color: "#a8a29e"
                font.pixelSize: 13
                font.letterSpacing: 0.6
            }
            Label {
                text: "Fila do dia"
                color: "#fafaf9"
                font.pixelSize: 28
                font.weight: Font.Medium
            }
            Label {
                text: "Aprove o texto do follow-up ou indique a quem pertence a pendência."
                color: "#a8a29e"
                font.pixelSize: 15
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            TextField {
                id: searchField
                Layout.fillWidth: true
                placeholderText: "Quando falei com o cliente sobre o tema"
                color: "#fafaf9"
                font.pixelSize: 14
                implicitHeight: 40
                leftPadding: 12
                rightPadding: 12
                onTextChanged: {
                    if (controller)
                        controller.clearSearchCandidates()
                }
                background: Rectangle {
                    radius: 8
                    color: "#292524"
                    border.color: "#44403c"
                }
            }
            Button {
                text: "Buscar"
                implicitHeight: 40
                leftPadding: 18
                rightPadding: 18
                font.pixelSize: 14
                font.capitalization: Font.MixedCase
                onClicked: controller.search(searchField.text)
                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: "#fafaf9"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 8
                    color: parent.down ? "#44403c" : parent.hovered ? "#57534e" : "#44403c"
                }
            }
            Button {
                text: "Parear"
                implicitHeight: 40
                leftPadding: 18
                rightPadding: 18
                font.pixelSize: 14
                font.capitalization: Font.MixedCase
                onClicked: controller.pair_whatsapp()
                contentItem: Text {
                    text: parent.text
                    font: parent.font
                    color: "#fafaf9"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 8
                    color: parent.down ? "#44403c" : parent.hovered ? "#57534e" : "#44403c"
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: searchCandidatesView.count > 0
            spacing: 6

            Label {
                text: "Escolha o cliente:"
                color: "#a8a29e"
                font.pixelSize: 14
            }
            ListView {
                id: searchCandidatesView
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(count * 40, 160)
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: controller ? controller.searchCandidates : null
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: Button {
                    required property int clientId
                    required property string name
                    width: searchCandidatesView.width
                    height: 40
                    text: name + " (ID: " + clientId + ")"
                    onClicked: controller.selectSearchCandidate(searchField.text, clientId)
                }
            }
        }

        Label {
            visible: controller.whatsappNotice.length > 0
            text: controller.whatsappNotice
            color: "#fbbf24"
            font.pixelSize: 14
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            visible: controller.notice.length > 0
            text: controller.notice
            color: "#fbbf24"
            font.pixelSize: 14
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Image {
            visible: controller.qrImage.length > 0
            source: controller.qrImage.length > 0 ? controller.qrImage : ""
            Layout.preferredWidth: 180
            Layout.preferredHeight: 180
            fillMode: Image.PreserveAspectFit
        }

        GridLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: 2
            columnSpacing: 20
            rowSpacing: 20

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 260
                radius: 16
                color: "#1c1917"
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 14

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "Rascunhos"
                            color: "#fafaf9"
                            font.pixelSize: 18
                            font.weight: Font.Medium
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: draftsView.count
                            color: "#a8a29e"
                            font.pixelSize: 14
                        }
                    }
                    Label {
                        text: "Textos aguardando a sua aprovação."
                        color: "#a8a29e"
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 96
                        implicitHeight: 96

                        ListView {
                            id: draftsView
                            anchors.fill: parent
                            clip: true
                            spacing: 10
                            boundsBehavior: Flickable.StopAtBounds
                            model: controller.drafts

                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                            delegate: Rectangle {
                                id: draftCard
                                required property int draftId
                                required property string text
                                required property string address
                                required property string channel
                                required property string entryClient
                                required property string contactClient
                                required property bool clientsMatch
                                required property string approvalVersion
                                required property string state
                                required property string subject
                                width: draftsView.width
                                implicitHeight: Math.max(draftRow.implicitHeight + 20, 64)
                                radius: 10
                                color: draftHover.hovered ? "#32302e" : "#292524"
                                border.color: "#44403c"

                                HoverHandler { id: draftHover }

                                RowLayout {
                                    id: draftRow
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.margins: 12
                                    spacing: 16

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 4

                                        Label {
                                            visible: draftCard.subject !== ""
                                            text: "Assunto: " + draftCard.subject
                                            color: "#d6d3d1"
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                        Label {
                                            text: draftCard.text
                                            color: "#fafaf9"
                                            font.pixelSize: 15
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                        Label {
                                            text: draftCard.address
                                            color: "#d6d3d1"
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                        Label {
                                            text: draftCard.channel
                                            color: "#a8a29e"
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                        Label {
                                            text: "Cliente da entrada: " + draftCard.entryClient
                                            color: "#d6d3d1"
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                        Label {
                                            text: "Cliente do contato: " + draftCard.contactClient
                                            color: "#d6d3d1"
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                        Label {
                                            visible: !draftCard.clientsMatch
                                            text: "Clientes diferentes: aprovação bloqueada."
                                            color: "#fbbf24"
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                        Label {
                                            visible: draftCard.state === "em curso"
                                            text: "Em curso"
                                            color: "#facc15"
                                            font.pixelSize: 13
                                            wrapMode: Text.WordWrap
                                            Layout.fillWidth: true
                                        }
                                    }
                                    Button {
                                        visible: draftCard.state === "aguardando"
                                        enabled: draftCard.clientsMatch
                                        text: "Aprovar"
                                        implicitHeight: 40
                                        leftPadding: 18
                                        rightPadding: 18
                                        font.pixelSize: 14
                                        font.capitalization: Font.MixedCase
                                        onClicked: controller.approve(draftId, approvalVersion)
                                        contentItem: Text {
                                            text: parent.text
                                            font: parent.font
                                            color: "#f7fee7"
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        background: Rectangle {
                                            radius: 8
                                            color: parent.down ? "#365314" : parent.hovered ? "#4d7c0f" : "#3f6212"
                                        }
                                    }
                                }
                            }
                        }

                        Label {
                            anchors.centerIn: parent
                            width: Math.min(parent.width - 32, 360)
                            visible: draftsView.count === 0
                            text: "Nenhum rascunho aguardando aprovação."
                            color: "#a8a29e"
                            font.pixelSize: 15
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 320
                radius: 16
                color: "#1c1917"
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 14

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "Pendências"
                            color: "#fafaf9"
                            font.pixelSize: 18
                            font.weight: Font.Medium
                        }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: pendingsView.count
                            color: "#a8a29e"
                            font.pixelSize: 14
                        }
                    }
                    Label {
                        text: "Escolha uma e indique o cliente. O processo entra quando couber."
                        color: "#a8a29e"
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 96
                        implicitHeight: 96

                        ListView {
                            id: pendingsView
                            anchors.fill: parent
                            clip: true
                            spacing: 10
                            boundsBehavior: Flickable.StopAtBounds
                            model: controller.pendings

                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                            delegate: Rectangle {
                                id: pendingCard
                                required property int entryId
                                required property string text
                                required property string reason
                                required property bool unowned
                                width: pendingsView.width
                                implicitHeight: Math.max(pendingText.implicitHeight + 24, 64)
                                radius: 10
                                color: window.selectedEntry === entryId ? "#3f2e14" : pendingHover.hovered ? "#32302e" : "#292524"
                                border.color: window.selectedEntry === entryId ? "#a16207" : "#44403c"
                                border.width: window.selectedEntry === entryId ? 1.5 : 1

                                HoverHandler { id: pendingHover }

                                Column {
                                    id: pendingText
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.margins: 12
                                    spacing: 4

                                    Label {
                                        width: parent.width
                                        text: pendingCard.text
                                        color: "#fafaf9"
                                        font.pixelSize: 15
                                        wrapMode: Text.WordWrap
                                    }
                                    Label {
                                        width: parent.width
                                        visible: reason !== ""
                                        text: reason
                                        color: "#d6d3d1"
                                        font.pixelSize: 13
                                        wrapMode: Text.WordWrap
                                    }
                                }

                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        window.selectedEntry = entryId
                                        window.selectedUnowned = unowned
                                    }
                                }
                            }
                        }

                        Label {
                            anchors.centerIn: parent
                            width: Math.min(parent.width - 32, 360)
                            visible: pendingsView.count === 0
                            text: "Nenhuma pendência para indicar."
                            color: "#a8a29e"
                            font.pixelSize: 15
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Label {
                            text: "Cliente"
                            color: "#d6d3d1"
                            font.pixelSize: 13
                        }
                        ComboBox {
                            id: clients
                            Layout.fillWidth: true
                            implicitHeight: 42
                            model: controller.clients
                            textRole: "name"
                            valueRole: "clientId"
                            font.pixelSize: 14
                            onCountChanged: if (count > 0 && currentIndex < 0) currentIndex = 0
                            onCurrentValueChanged: {
                                if (currentValue !== undefined)
                                    controller.selectClient(currentValue)
                            }
                        }

                        Label {
                            text: "Processo"
                            color: "#d6d3d1"
                            font.pixelSize: 13
                        }
                        ComboBox {
                            id: processes
                            Layout.fillWidth: true
                            implicitHeight: 42
                            model: controller.processes
                            textRole: "label"
                            valueRole: "processId"
                            font.pixelSize: 14
                            onCountChanged: if (count > 0 && currentIndex < 0) currentIndex = 0
                        }

                        Button {
                            text: "Indicar"
                            Layout.fillWidth: true
                            implicitHeight: 40
                            font.pixelSize: 14
                            font.capitalization: Font.MixedCase
                            enabled: window.selectedEntry >= 0 && clients.currentIndex >= 0
                            onClicked: {
                                if (controller.indicate(window.selectedEntry, clients.currentValue, window.processChoice()))
                                    window.selectedEntry = -1
                            }
                        }

                        Label {
                            text: "Novo cliente"
                            color: "#d6d3d1"
                            font.pixelSize: 13
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8

                            TextField {
                                id: newClientName
                                Layout.fillWidth: true
                                placeholderText: "Nome"
                                color: "#fafaf9"
                                font.pixelSize: 14
                                implicitHeight: 40
                                leftPadding: 12
                                rightPadding: 12
                                background: Rectangle {
                                    radius: 8
                                    color: "#292524"
                                    border.color: "#44403c"
                                }
                            }
                            ComboBox {
                                id: newClientStatus
                                implicitHeight: 40
                                font.pixelSize: 14
                                model: ["cliente", "prospecção"]
                            }
                            Button {
                                text: "Criar"
                                implicitHeight: 40
                                font.pixelSize: 14
                                font.capitalization: Font.MixedCase
                                enabled: newClientName.text.trim() !== ""
                                onClicked: {
                                    var created = controller.createClient(newClientName.text, newClientStatus.currentText)
                                    if (created > 0) {
                                        newClientName.text = ""
                                        clients.currentIndex = clients.indexOfValue(created)
                                    }
                                }
                            }
                        }

                        Button {
                            text: "Pessoal: apagar e ignorar este contato"
                            Layout.fillWidth: true
                            implicitHeight: 40
                            font.pixelSize: 14
                            font.capitalization: Font.MixedCase
                            enabled: window.selectedEntry >= 0 && window.selectedUnowned
                            onClicked: personalDialog.open()
                        }
                    }
                }
            }
        }
    }

    Dialog {
        id: personalDialog
        anchors.centerIn: parent
        modal: true
        title: "Marcar como pessoal"
        standardButtons: Dialog.Ok | Dialog.Cancel
        Label {
            width: 360
            wrapMode: Text.WordWrap
            text: "As entradas pendentes deste contato e os anexos delas serão apagados. As próximas mensagens dele não entram no flow."
        }
        onAccepted: {
            if (controller.markPersonal(window.selectedEntry)) {
                window.selectedEntry = -1
                window.selectedUnowned = false
            }
        }
    }
}
