
#include <Arduino.h>
#include <esp_arduino_version.h>

// =============================================
// OLYMPUS21 - ESP32 BLACKJACK CONTROLLER
// NFC + BETTING + GAMEPLAY + LED + PASSIVE BUZZER
// =============================================

// BUTTON PINS
// 25 = HIT / $10
// 14 = STAND / $20
// 27 = DOUBLE / $50
// 26 = SPLIT / $100
// 18 = PLACE BET

const int buttonPins[5] = {
  25, 14, 27, 26, 18
};

const int chipValues[4] = {
  10, 20, 50, 100
};

const char* actions[4] = {
  "HIT",
  "STAND",
  "DOUBLE",
  "SPLIT"
};

// OUTPUT PINS
const int GREEN_LED = 32;
const int RED_LED = 33;
const int BUZZER = 23;

// Change to false to mute
const bool SOUND_ENABLED = true;

// =============================================
// BUTTON DEBOUNCING
// =============================================

bool stableState[5];
bool lastReading[5];

unsigned long lastChange[5] = {0};
const unsigned long debounceMs = 40;

// =============================================
// CONTROLLER MODES
// =============================================

enum ControllerMode {
  WAIT_NFC,
  BETTING,
  PLAYING,
  RESULT
};

ControllerMode mode = WAIT_NFC;

enum ResultType {
  NONE,
  WIN,
  LOSS,
  PUSH
};

ResultType result = NONE;

bool confirmationPending = false;

String incoming = "";

// =============================================
// PASSIVE BUZZER SOUND SYSTEM
// =============================================

// Each step has a frequency and duration.
// Frequency 0 means silence.

struct SoundStep {
  uint16_t frequency;
  uint16_t duration;
};

// =============================================
// WINNING SOUND - CELEBRATORY ARCADE FANFARE
// =============================================

const SoundStep winSound[] = {
  {784, 95},      // PA
  {0, 35},
  {988, 95},      // PA
  {0, 35},
  {1175, 110},    // RA
  {1568, 330}     // TAAA!
};

// =============================================
// LOSING SOUND - EXACTLY TWO BEEPS
// =============================================

const SoundStep loseSound[] = {
  {900, 180},
  {0, 160},
  {700, 200}
};

// Sound playback variables
const SoundStep* currentSound = nullptr;

int soundLength = 0;
int soundStep = 0;

bool soundActive = false;
unsigned long soundTimer = 0;

// =============================================
// ESP32 PWM BUZZER CONFIGURATION
// =============================================

const int BUZZER_CHANNEL = 0;

void initBuzzer() {

#if ESP_ARDUINO_VERSION_MAJOR >= 3

  ledcAttach(BUZZER, 2000, 8);
  ledcWriteTone(BUZZER, 0);

#else

  ledcSetup(BUZZER_CHANNEL, 2000, 8);
  ledcAttachPin(BUZZER, BUZZER_CHANNEL);
  ledcWriteTone(BUZZER_CHANNEL, 0);

#endif
}

void setTone(uint32_t frequency) {

#if ESP_ARDUINO_VERSION_MAJOR >= 3

  ledcWriteTone(BUZZER, frequency);

#else

  ledcWriteTone(BUZZER_CHANNEL, frequency);

#endif
}

// =============================================
// SOUND PLAYBACK
// =============================================

void stopSound() {
  soundActive = false;
  setTone(0);
}

void playSound(
  const SoundStep* pattern,
  int length
) {

  stopSound();

  if (!SOUND_ENABLED || length <= 0) {
    return;
  }

  currentSound = pattern;
  soundLength = length;

  soundStep = 0;
  soundTimer = millis();

  soundActive = true;

  setTone(currentSound[0].frequency);
}

void updateSound() {

  if (!soundActive) {
    return;
  }

  if (
    millis() - soundTimer >=
    currentSound[soundStep].duration
  ) {

    soundStep++;

    if (soundStep >= soundLength) {
      stopSound();
      return;
    }

    soundTimer = millis();

    setTone(currentSound[soundStep].frequency);
  }
}

// =============================================
// LED CONTROL
// =============================================

void updateLEDs() {

  switch (mode) {

    case WAIT_NFC:

      digitalWrite(GREEN_LED, LOW);
      digitalWrite(RED_LED, LOW);

      break;

    case BETTING:

      // Slow green blinking
      digitalWrite(
        GREEN_LED,
        (millis() / 800) % 2
      );

      digitalWrite(RED_LED, LOW);

      break;

    case PLAYING:

      // Normal green blinking
      digitalWrite(
        GREEN_LED,
        (millis() / 500) % 2
      );

      digitalWrite(RED_LED, LOW);

      break;

    case RESULT:

      if (result == WIN) {

        digitalWrite(GREEN_LED, HIGH);
        digitalWrite(RED_LED, LOW);

      }

      else if (result == LOSS) {

        digitalWrite(GREEN_LED, LOW);
        digitalWrite(RED_LED, HIGH);

      }

      else {

        digitalWrite(GREEN_LED, LOW);
        digitalWrite(RED_LED, LOW);

      }

      break;
  }
}

// =============================================
// COMMANDS FROM PYTHON BACKEND
// =============================================

void handleCommand(String command) {

  command.trim();

  // Wait for player NFC tag
  if (command == "MODE:WAIT_NFC") {

    mode = WAIT_NFC;
    result = NONE;

    confirmationPending = false;

    stopSound();
  }

  // NFC successfully scanned
  else if (command == "MODE:BETTING") {

    mode = BETTING;
    result = NONE;

    confirmationPending = false;

    stopSound();
  }

  // Bet accepted by backend
  else if (command == "MODE:PLAYING") {

    mode = PLAYING;
    result = NONE;

    confirmationPending = false;

    stopSound();
  }

  // Invalid bet or insufficient balance
  else if (command == "BET_REJECT") {

    if (mode == BETTING) {
      confirmationPending = false;
    }
  }

  // WIN: Celebratory melody
  else if (
    command == "RESULT:WIN" ||
    command == "RESULT:BLACKJACK"
  ) {

    mode = RESULT;
    result = WIN;

    playSound(
      winSound,
      sizeof(winSound) / sizeof(winSound[0])
    );
  }

  // LOSS: Two beeps
  else if (
    command == "RESULT:BUST" ||
    command == "RESULT:LOSE"
  ) {

    mode = RESULT;
    result = LOSS;

    playSound(
      loseSound,
      sizeof(loseSound) / sizeof(loseSound[0])
    );
  }

  // TIE: No sound
  else if (command == "RESULT:PUSH") {

    mode = RESULT;
    result = PUSH;

    stopSound();
  }
}

// =============================================
// SERIAL COMMUNICATION
// =============================================

void readSerialCommands() {

  while (Serial.available()) {

    char c = Serial.read();

    if (c == '\n') {

      handleCommand(incoming);

      incoming = "";
    }

    else if (c != '\r') {

      if (incoming.length() < 64) {
        incoming += c;
      }
    }
  }
}

// =============================================
// BUTTON HANDLING
// =============================================

void handleButton(int i) {

  // BETTING MODE
  if (mode == BETTING) {

    if (confirmationPending) {
      return;
    }

    // Four betting buttons
    if (i < 4) {

      Serial.print("BET_ADD:");
      Serial.println(chipValues[i]);
    }

    // Fifth button confirms wager
    else if (i == 4) {

      Serial.println("BET_CONFIRM");

      confirmationPending = true;
    }
  }

  // PLAYING MODE
  else if (mode == PLAYING) {

    if (i < 4) {
      Serial.println(actions[i]);
    }
  }

  // Buttons ignored in WAIT_NFC and RESULT
}

// =============================================
// SETUP
// =============================================

void setup() {

  Serial.begin(115200);

  pinMode(GREEN_LED, OUTPUT);
  pinMode(RED_LED, OUTPUT);
  pinMode(BUZZER, OUTPUT);

  digitalWrite(GREEN_LED, LOW);
  digitalWrite(RED_LED, LOW);
  digitalWrite(BUZZER, LOW);

  // Initialize PWM for passive buzzer
  initBuzzer();

  // Initialize five buttons
  for (int i = 0; i < 5; i++) {

    pinMode(
      buttonPins[i],
      INPUT_PULLUP
    );

    stableState[i] =
      digitalRead(buttonPins[i]);

    lastReading[i] =
      stableState[i];
  }

  Serial.println("OLYMPUS21 READY!");
}

// =============================================
// MAIN LOOP
// =============================================

void loop() {

  // Read Python backend commands
  readSerialCommands();

  // Scan five pushbuttons
  for (int i = 0; i < 5; i++) {

    bool reading =
      digitalRead(buttonPins[i]);

    if (reading != lastReading[i]) {
      lastChange[i] = millis();
    }

    if (
      millis() - lastChange[i] >
      debounceMs
    ) {

      if (reading != stableState[i]) {

        stableState[i] = reading;

        if (stableState[i] == LOW) {

          handleButton(i);
        }
      }
    }

    lastReading[i] = reading;
  }

  updateLEDs();
  updateSound();
}
