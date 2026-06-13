# Wiring — lexa (Viam Rover 1 + MAX98357A audio)

Hardware wiring for the **lexa** voice/video assistant running on a **Viam Rover 1**
chassis with a **MAX98357A** I2S amplifier added for speaker output.

- **Board:** Raspberry Pi 3 Model B (the Viam Rover 1 docs assume a Pi 4, but the
  40-pin header pinout is identical).
- **Pin numbering:** "Pin" = physical board pin (1–40); "GPIO" = BCM number.
  Viam board configs use **physical pin numbers**.

---

## ⚠️ The GPIO19 conflict (read this first)

The MAX98357A is an I2S device. The Raspberry Pi's I2S/PCM peripheral hardwires
its signals to fixed GPIOs that **cannot be remapped**:

| I2S signal | GPIO | Physical pin |
|---|---|---|
| BCLK (bit clock) | GPIO18 | 12 |
| **LRCLK (word/frame select)** | **GPIO19** | **35** |
| DOUT → amp DIN | GPIO21 | 40 |

The **stock Viam Rover 1** wiring puts the **Left motor encoder on pin 35 (GPIO19)** —
the same pin the I2S amp needs for LRCLK.

**Resolution:** an encoder is just a digital input and can live on *any* GPIO, so we
move it; LRCLK stays on GPIO19.

- **Encoder Left: moved from pin 35 (GPIO19) → pin 29 (GPIO5).**
- LRCLK (MAX98357A `LRC`) now occupies pin 35 (GPIO19).
- The Viam encoder config must be updated to match (see "Viam config" below).

> Symptom if LRCLK is *not* on GPIO19: test tones sound roughly OK, but **speech is
> garbled/unintelligible** even though the audio data is clean (a jittery/absent
> frame-sync survives a single tone but destroys complex speech).

---

## Full pin map (as built)

### Viam Rover 1 components

| Component | Component Pin | Phys. Pin | GPIO | Wire | Notes |
|---|---|---|---|---|---|
| Buck Converter | GND | 39 | GND | black | |
| Buck Converter | 5V | 4 | 5V | red | |
| Accelerometer (ADXL345, I2C) | GND | 34 | GND | black | |
| Accelerometer | 3.3V | 17 | 3V3 | red | |
| Accelerometer | SDA | 3 | GPIO2 | maroon | I2C1 SDA |
| Accelerometer | SCL | 5 | GPIO3 | pink | I2C1 SCL |
| L298N Motor Driver | En B | 22 | GPIO25 | gray | |
| L298N Motor Driver | In 4 | 18 | GPIO24 | yellow | |
| L298N Motor Driver | In 3 | 16 | GPIO23 | white | |
| L298N Motor Driver | In 2 | 13 | GPIO27 | green | |
| L298N Motor Driver | In 1 | 11 | GPIO17 | blue | |
| L298N Motor Driver | En A | 15 | GPIO22 | purple | |
| L298N Motor Driver | GND | 6 | GND | black | |
| L298N Motor Driver | 3.3V | 1 | 3V3 | red | |
| L298N Motor Driver | **Encoder Left** | **29** ⟵ moved | **GPIO5** | yellow | **was pin 35/GPIO19** |
| L298N Motor Driver | Encoder Right | 37 | GPIO26 | white | |

### MAX98357A I2S amplifier (added)

| MAX98357A Pin | Phys. Pin | GPIO | Notes |
|---|---|---|---|
| `LRC` (LRCLK) | 35 | GPIO19 | I2S word select — freed by moving Encoder Left |
| `BCLK` | 12 | GPIO18 | I2S bit clock |
| `DIN` | 40 | GPIO21 | I2S data in |
| `Vin` | 2 | 5V | 5V (pin 4 already used by buck converter, use pin 2) |
| `GND` | 9 | GND | any free ground (9/14/20/25/30) |
| `GAIN` | — | — | leave floating = 9 dB; **tie to `Vin` = 6 dB (less hiss)**; 100 kΩ→`Vin` = 3 dB |
| `SD` | — | — | pull high / tie to `Vin` for always-on (Adafruit breakout: leave default) |

**Free pins** (unused, available for expansion): GPIO5 now holds the encoder; still
free → pin 31 (GPIO6), 32 (GPIO12), 33 (GPIO13), 36 (GPIO16), and the SPI0 group
(19/21/23/24/26) if SPI is unused.

---

## Software configuration

### 1. Enable the I2S DAC (`/boot/firmware/config.txt`)

```ini
# MAX98357A I2S DAC/amp (hifiberry-dac overlay)
dtoverlay=hifiberry-dac
```

The MAX98357A speaks plain I2S, so the generic `hifiberry-dac` overlay drives it —
no SD-pin GPIO setup required. Reboot after editing.

### 2. Default audio output (`/etc/asound.conf`)

```
pcm.!default {
    type plug                       # auto-resample 24kHz mono -> 48kHz stereo
    slave.pcm { type hw card "sndrpihifiberry" }
}
ctl.!default { type hw card "sndrpihifiberry" }
```

### 3. Viam encoder config

Update the encoder (or encoded-motor) component so the **left encoder pin = `29`**
(was `35`). Example fragment:

```json
{
  "name": "lenc",
  "type": "encoder",
  "model": "incremental",
  "attributes": { "pins": { "i": "29" }, "board": "local" }
}
```

(Pin numbers in Viam are physical board pins. Adjust the model/attribute names to
match your existing config — only the pin value changes: `35` → `29`.)

---

## Verification

```bash
# audio card present?
aplay -l | grep hifiberry            # -> card 1: sndrpihifiberry

# tone out of the MAX98357A
speaker-test -D default -t sine -f 440 -c 2 -l 1

# full client (mic + webcam + MAX98357A speaker)
cd ~/lexa && python3 client-video.py

# encoder moved? confirm the rover reads left-wheel motion in the Viam app
```

If speech is garbled, re-check that **LRCLK is physically on pin 35 (GPIO19)** and the
encoder wire is on pin 29 — this was the original root cause.
