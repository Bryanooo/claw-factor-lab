"""Create local secrets without printing them; never overwrite existing configuration."""
from pathlib import Path
import secrets
p=Path(__file__).resolve().parents[1]/'.env'
with p.open('x') as f:
    f.write('POSTGRES_PASSWORD='+secrets.token_urlsafe(32)+'\n')
p.chmod(0o600)
print('Created .env (mode 0600).')
