param([string]$Path)
$bytes = [System.IO.File]::ReadAllBytes($Path)
"Total: $($bytes.Length)"
"BOM: $(($bytes[0] -eq 0xEF) -and ($bytes[1] -eq 0xBB) -and ($bytes[2] -eq 0xBF))"
$invalid = 0
$i = 0
while ($i -lt $bytes.Length) {
  $b = $bytes[$i]
  if ($b -lt 0x80) { $i++; continue }
  if ($b -ge 0xC2 -and $b -lt 0xE0) {
    if ($i+1 -ge $bytes.Length -or ($bytes[$i+1] -band 0xC0) -ne 0x80) { $invalid++; "invalid 2byte at $i" }
    $i += 2; continue
  }
  if ($b -ge 0xE0 -and $b -lt 0xF0) {
    if ($i+2 -ge $bytes.Length -or ($bytes[$i+1] -band 0xC0) -ne 0x80 -or ($bytes[$i+2] -band 0xC0) -ne 0x80) { $invalid++; "invalid 3byte at $i" }
    $i += 3; continue
  }
  if ($b -ge 0xF0 -and $b -lt 0xF5) {
    $i += 4; continue
  }
  $invalid++
  "stray byte at offset $i = 0x$('{0:X2}' -f $b)"
  $i++
}
"Invalid sequences: $invalid"
