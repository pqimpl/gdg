# ====================================================
# Level 3 - The Web-Slinger's Secret
# ====================================================

PHASE 1 — CHECK THE FILE
------------------------

The image is damaged.

Inspect its internal structure and find the section
whose checksum does not match its contents.


PHASE 2 — REPAIR IT
-------------------

Locate the damaged bytes.

Calculate the correct value and replace only those
bytes. The image should become readable again.


PHASE 3 — FIND THE HIDDEN DATA
------------------------------

There are two hidden pieces.

One is inside the image's metadata.

The other is appended after the normal end of the
image file.

Find both pieces and determine their correct order.


PHASE 4 — DECODE
-----------------

Combine the two pieces.

The result is encoded in a common text-safe format.

Decode it.

What you get is still encrypted.


PHASE 5 — FIND THE KEY
----------------------

The key is not stored anywhere as text.

Calculate it from the image itself using:

    - raw pixel data
    - image width
    - image height

Hash the resulting data to produce the key.


PHASE 6 — DECRYPT
-----------------

Use the calculated key with the encrypted data.

The operation is reversible.

The result will be another encoded string.


PHASE 7 — FINAL DECODE
----------------------

The final string will consist mostly of uppercase
letters and digits, possibly ending with "=".

Recognize the encoding, decode it, and recover
the flag.