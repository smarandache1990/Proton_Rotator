import re

# Read your source text file (replace with the actual file path)
with open('protonvpn_source.txt', 'r') as f:
    text = f.read()

# Find all server codes in format XX#N
server_codes = re.findall(r'\b[A-Z]{2}#[\d]+', text)

# Sort alphabetically by country code then number
server_codes.sort()

# Save to new file
with open('protonvpn_complete.txt', 'w') as f:
    f.write('\n'.join(sorted(server_codes)))

print(f"Extracted {len(server_codes)} server codes")