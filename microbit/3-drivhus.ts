// =====================================================================
// Eksempel: DRIVHUS  (model DH)
//  - Fugtsensor på P0 -> DH.fugt (0-100 %)
//  - Temperatur      -> DH.temp
//  - Byen svarer med DH.pumpe = 1/0 -> relæ/pumpe på P1
// =====================================================================
radio.setGroup(1)
radio.setTransmitSerialNumber(true)

basic.forever(function () {
    let fugt = Math.map(pins.analogReadPin(AnalogPin.P0), 0, 1023, 0, 100)
    radio.sendValue("DH.fugt", Math.round(fugt))
    radio.sendValue("DH.temp", input.temperature())
    basic.pause(2000)
})

radio.onReceivedValue(function (name, value) {
    if (name == "DH.pumpe") {
        pins.digitalWritePin(DigitalPin.P1, value)
        if (value == 1) basic.showIcon(IconNames.Umbrella) else basic.clearScreen()
    }
})
