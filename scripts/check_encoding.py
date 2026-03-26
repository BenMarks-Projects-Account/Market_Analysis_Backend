"""Fix double-encoded UTF-8 in home.js and any other affected files.

Double-encoding: original UTF-8 bytes read as CP1252, then re-encoded to UTF-8.
Fix: for each non-ASCII run, encode as CP1252 to recover original bytes, decode as UTF-8.
"""
import os
import sys

# Build a complete reverse mapping: Unicode codepoint → CP1252 byte value.
# This handles the "undefined" positions (0x81, 0x8D, 0x8F, 0x90, 0x9D) that
# Python's cp1252 codec can't encode — we add them manually as pass-through.
_CP1252_TO_BYTE = {}
for _b in range(256):
    try:
        _ch = bytes([_b]).decode('cp1252')
        _CP1252_TO_BYTE[ord(_ch)] = _b
    except Exception:
        pass
# Add the undefined CP1252 positions (C1 controls) that Python maps pass-through on decode
# but refuses to encode: 0x81→U+0081, 0x8D→U+008D, 0x8F→U+008F, 0x90→U+0090, 0x9D→U+009D
for _b in (0x81, 0x8D, 0x8F, 0x90, 0x9D):
    _CP1252_TO_BYTE[_b] = _b


def _encode_cp1252_full(text):
    """Encode text to bytes using complete CP1252 mapping (including undefined positions)."""
    result = bytearray()
    for ch in text:
        cp = ord(ch)
        if cp in _CP1252_TO_BYTE:
            result.append(_CP1252_TO_BYTE[cp])
        elif cp < 256:
            result.append(cp)
        else:
            raise ValueError(f'U+{cp:04X} not in CP1252 range')
    return bytes(result)


def fix_double_encoded_utf8(text):
    """Fix text where UTF-8 bytes were misread as CP1252 then re-encoded as UTF-8."""
    result = []
    i = 0
    n = len(text)
    while i < n:
        if ord(text[i]) > 127:
            # Collect contiguous non-ASCII characters
            j = i
            while j < n and ord(text[j]) > 127:
                j += 1
            chunk = text[i:j]
            try:
                raw_bytes = _encode_cp1252_full(chunk)
                fixed = raw_bytes.decode('utf-8')
                result.append(fixed)
            except (ValueError, UnicodeDecodeError):
                # Not double-encoded or can't fix — keep original
                result.append(chunk)
            i = j
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


def process_file(filepath):
    """Fix double-encoded UTF-8 in a single file. Returns True if changes were made."""
    with open(filepath, 'r', encoding='utf-8') as f:
        original = f.read()
    
    fixed = fix_double_encoded_utf8(original)
    
    if fixed != original:
        # Strip any stray BOM characters that shouldn't be in JS files
        fixed = fixed.replace('\ufeff', '')
        with open(filepath, 'w', encoding='utf-8', newline='') as f:
            f.write(fixed)
        return True
    return False


if __name__ == '__main__':
    root = os.path.join(os.path.dirname(__file__), '..', 'BenTrade', 'frontend', 'assets', 'js')
    
    # Primary target
    home_js = os.path.join(root, 'pages', 'home.js')
    
    if process_file(home_js):
        print(f'FIXED: {home_js}')
    else:
        print(f'OK (no changes): {home_js}')
    
    # Check all other JS files for the same issue
    checked = 0
    fixed_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith('.js'):
                continue
            fp = os.path.join(dirpath, fn)
            if os.path.samefile(fp, home_js):
                continue
            checked += 1
            if process_file(fp):
                fixed_files.append(fp)
    
    print(f'\nScanned {checked} other JS files.')
    if fixed_files:
        print(f'Fixed {len(fixed_files)} additional files:')
        for f in fixed_files:
            print(f'  FIXED: {f}')
    else:
        print('No other files needed fixing.')
