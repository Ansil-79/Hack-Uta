# NFC Blackjack

The chip stores an ID and display name as an NDEF **Text** record. MongoDB stores the player's demo balance. A tap loads that balance into the browser. Deal, Double, and Split debit MongoDB, and round settlement credits the payout. Ending the session returns the browser to the tap screen.

## Setup

1. In MongoDB Atlas, create a database user with read/write access to your demo database and allow the demo machine's IP. Copy the Python driver connection string and substitute the database user's credentials.
2. Create the `hack_uta` database and the `players` collection. Insert this document in Atlas Data Explorer:

   ```json
   { "_id": "P001", "name": "Ansil", "balance": 250, "revision": 0 }
   ```

   Use a unique string ID for each player. `balance` is a whole number of demo credits. Start `revision` at zero. To use a different database, set `MONGODB_DATABASE`.

3. Connect the ACR122U. In NFC Tools, write a **Text** record to the NTAG215:

   ```json
   { "id": "P001", "name": "Ansil" }
   ```

   Keep the record compact. The reader reads the first 144 bytes of user memory. Replace any old `BALANCE:250` text before tapping.

4. In the VS Code terminal, install the dependencies and start the server:

   ```bash
   python3 -m pip install pyscard websockets "pymongo[srv]>=4.2,<5"
   read -rs 'MONGODB_URI?MongoDB URI: '
   export MONGODB_URI
   python3 nfc_server.py
   ```

   The `read` command above is for macOS zsh. Paste the URI at the prompt and press Return. Keep credentials out of Git. Open `index.html` in your browser on the same computer.

## Verify the demo

Tap P001 and check that the HUD shows Ansil and $250. Choose a bet and click Deal. Check that MongoDB shows the deducted balance. Finish the round and check the payout, then end the session and retap to verify the balance persisted. A legacy chip or an unknown player ID should show an error.

## Demo scope

Use one browser per physical table and demo credits only. Blackjack outcomes are calculated in the browser and chips can be copied. A reload during a hand ends the browser's round state. Confirmed debits remain in MongoDB. Out-of-band balance edits should advance `revision` so an older browser snapshot cannot apply an update.
