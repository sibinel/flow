import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: window
    visible: true
    width: 960
    height: 640
    title: "flow"
    color: "#f7f5f2"

    property int selectedEntry: -1

    function processChoice() {
        if (processes.currentIndex < 0 || processes.currentValue === undefined)
            return 0
        return processes.currentValue
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 20

        Label {
            text: "fila"
            font.pixelSize: 22
            color: "#1c1917"
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 28

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8

                Label {
                    text: "follow-up para aprovar"
                    color: "#1c1917"
                }

                ListView {
                    id: draftsView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 8
                    model: controller.drafts

                    delegate: Rectangle {
                        required property int draftId
                        required property string text
                        width: draftsView.width
                        implicitHeight: draftRow.implicitHeight + 16
                        radius: 8
                        color: "#ffffff"
                        border.color: "#e7e5e4"

                        RowLayout {
                            id: draftRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 8
                            Label {
                                text: text
                                color: "#1c1917"
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            Button {
                                text: "aprovar"
                                onClicked: controller.approve(draftId)
                            }
                        }
                    }
                }

                Label {
                    visible: draftsView.count === 0
                    text: "nenhum rascunho aguardando"
                    color: "#78716c"
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 8

                Label {
                    text: "pendência para indicar"
                    color: "#1c1917"
                }

                ListView {
                    id: pendingsView
                    Layout.fillWidth: true
                    Layout.preferredHeight: 220
                    clip: true
                    spacing: 8
                    model: controller.pendings

                    delegate: Rectangle {
                        required property int entryId
                        required property string text
                        required property string reason
                        width: pendingsView.width
                        implicitHeight: pendingText.implicitHeight + 20
                        radius: 8
                        color: window.selectedEntry === entryId ? "#fef9c3" : "#ffffff"
                        border.color: "#e7e5e4"

                        Label {
                            id: pendingText
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 10
                            text: reason === "" ? text : text + " — " + reason
                            color: "#1c1917"
                            wrapMode: Text.WordWrap
                        }

                        MouseArea {
                            anchors.fill: parent
                            onClicked: window.selectedEntry = entryId
                        }
                    }
                }

                Label {
                    visible: pendingsView.count === 0
                    text: "nenhuma pendência"
                    color: "#78716c"
                }

                ComboBox {
                    id: clients
                    Layout.fillWidth: true
                    model: controller.clients
                    textRole: "name"
                    valueRole: "clientId"
                    onCurrentValueChanged: {
                        if (currentValue !== undefined)
                            controller.selectClient(currentValue)
                    }
                }

                ComboBox {
                    id: processes
                    Layout.fillWidth: true
                    model: controller.processes
                    textRole: "label"
                    valueRole: "processId"
                }

                Button {
                    text: "indicar"
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
