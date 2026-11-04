// =====================================================================
// Eksempel: LYT TIL HELE BYEN
// En micro:bit der ikke sender noget, men viser hvad der sker:
//  - viser klokken (BY.tid) når den skifter
//  - spiller alarm ved oversvømmelse (OV.alarm)
// =====================================================================
radio.setGroup(1)

radio.onReceivedValue(function (name, value) {
    if (name == "BY.tid") {
        basic.showNumber(value)
    } else if (name == "OV.alarm" && value == 1) {
        basic.showIcon(IconNames.No)
        music.play(music.builtinPlayableSoundEffect(soundExpression.sad), music.PlaybackMode.InBackground)
    }
})
