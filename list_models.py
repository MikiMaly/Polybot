from dotenv import load_dotenv
load_dotenv()
import os, httpx

r = httpx.get(
    'https://openrouter.ai/api/v1/models',
    headers={'Authorization': f'Bearer {os.environ["OPENROUTER_API_KEY"]}'}
)
models = [m['id'] for m in r.json()['data'] if m['id'].endswith(':free')]
for m in sorted(models):
    print(m)
