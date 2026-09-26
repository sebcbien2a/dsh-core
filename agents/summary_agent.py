import json
import urllib.request

class SummaryAgent:
    def __init__(self, model_name="llama3.2"):
        self.model_name = model_name
        self.ollama_url = "http://localhost:11434/api/generate"

    def summarize(self, text_content: str) -> str:
        print(f"[SummaryAgent ({self.model_name})] Compression contextuelle en cours...")
        
        prompt = f"""Tu es un agent ultra-léger de résumé et de compression contextuelle.
Extraits les faits clés, les montants, les dates et les entités importantes sous forme de puces concises :

{text_content}"""

        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False
        }

        req = urllib.request.Request(
            self.ollama_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )

        try:
            with urllib.request.urlopen(req) as response:
                res = json.loads(response.read().decode('utf-8'))
                return res.get("response", "").strip()
        except Exception as e:
            return f"Erreur Ollama : {e}"