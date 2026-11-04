// =====================================================================
// Eksempel: GADELYS  (model GL)
//  - Sender lysniveau (GL.lys) hvert sekund
//  - Knap A = en fodgænger går forbi (GL.bev) – eller brug en PIR-sensor på P0
//  - Modtager GL.lampe (0-100 %) fra byen og styrer en LED på P1
// =====================================================================
radio.setGroup(1)
radio.setTransmitSerialNumber(true)   // VIGTIGT: så hjemmesiden kan kende micro:bit'en

let lampe = 0

basic.forever(function () {
    radio.sendValue("GL.lys", input.lightLevel())
    basic.pause(1000)
})

input.onButtonPressed(Button.A, function () {
    radio.sendValue("GL.bev", 1)
    basic.pause(5000)
    radio.sendValue("GL.bev", 0)
})

radio.onReceivedValue(function (name, value) {
    if (name == "GL.lampe") {
        lampe = value
        pins.analogWritePin(AnalogPin.P1, Math.map(lampe, 0, 100, 0, 1023))
        led.plotBarGraph(lampe, 100)
    }
})
