// =====================================================================
// Eksempel: SKRALDESPAND 1  (model S1)  – skift til "S2" for spand 2
//  - Knap A = der smides skrald i (+10 %)  (eller en afstandssensor i låget)
//  - Når skraldebilen har tømt spanden, sender byen S1.tom -> nulstil
// =====================================================================
radio.setGroup(1)
radio.setTransmitSerialNumber(true)
let fyld = 0

input.onButtonPressed(Button.A, function () {
    fyld = Math.min(100, fyld + 10)
})

basic.forever(function () {
    radio.sendValue("S1.fyld", fyld)
    led.plotBarGraph(fyld, 100)
    basic.pause(1000)
})

radio.onReceivedValue(function (name, value) {
    if (name == "S1.tom") {
        fyld = 0
        basic.showIcon(IconNames.Yes)
    }
})
