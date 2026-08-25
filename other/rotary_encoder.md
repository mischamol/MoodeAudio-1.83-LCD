I added a rotary encoder (KY-040) and ran into some pins being occupied by the argon case. After some trial and error and using `gpioinfo` I came up with the following

`switch: gpio 27 Cmd mpc,toggle`
`Rotary encoder settings: 100 2 3 17 24`

Taking up pins:
```
17 3v3
13 (GPIO 27) switch
11 (GPIO 17) data
18 (GPIO 24) clk
14 Ground
```
