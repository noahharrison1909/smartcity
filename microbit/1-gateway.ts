// =====================================================================
// SmartCity GATEWAY  –  sidder i USB på lærerens computer
// Indsæt i MakeCode (makecode.microbit.org) -> fanen "JavaScript".
//
// Radio  -> USB:  "serienummer,navn,værdi"   fx "-123456,GL.lys,120"
// USB    -> Radio: "navn,værdi"              fx "GL.lampe,100"
// =====================================================================
radio.setGroup(1)                     // alle micro:bits i klassen skal bruge samme gruppe
radio.setTransmitPower(7)
serial.setBaudRate(BaudRate.BaudRate115200)
serial.setRxBufferSize(254)          // plads til mange kommandoer i træk
basic.showIcon(IconNames.House)

// Data fra en model-micro:bit -> videre til hjemmesiden
radio.onReceivedValue(function (name, value) {
    let sn = radio.receivedPacket(RadioPacketProperty.SerialNumber)
    serial.writeLine("" + sn + "," + name + "," + value)
    led.toggle(0, 4)                  // blink nederst til venstre = modtaget
})

// Kommando fra hjemmesiden -> ud på radioen til alle model-micro:bits
serial.onDataReceived(serial.delimiters(Delimiters.NewLine), function () {
    let line = serial.readUntil(serial.delimiters(Delimiters.NewLine))
    let i = line.indexOf(",")
    if (i > 0) {
        let name = line.substr(0, i)
        let value = parseFloat(line.substr(i + 1))
        radio.sendValue(name, value)
        led.toggle(4, 4)              // blink nederst til højre = sendt
    }
})
