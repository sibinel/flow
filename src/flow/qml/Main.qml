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

        Label {
            visible: controller.whatsappNotice.length > 0
            text: controller.whatsappNotice
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
                                    }
                                    Button {
                                        text: "Aprovar"
                                        implicitHeight: 40
                                        leftPadding: 18
                                        rightPadding: 18
                                        font.pixelSize: 14
                                        font.capitalization: Font.MixedCase
                                        onClicked: controller.approve(draftId)
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
                                    onClicked: window.selectedEntry = entryId
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
                    }
                }
            }
        }
    }
}
