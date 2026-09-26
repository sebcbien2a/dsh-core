import os
import requests
import subprocess

class CoderAgent:
    def __init__(self, junior_model="qwen3.5:9b", senior_model="qwen3.6:27b"):
        self.api_url = "http://localhost:11434/api/generate"
        self.junior_model = junior_model
        self.senior_model = senior_model
        self.workspace_dir = "A:\\dsh-core\\workspace"
        os.makedirs(self.workspace_dir, exist_ok=True)

    def _call_llm(self, prompt, model):
        payload = {"model": model, "prompt": prompt, "stream": False}
        response = requests.post(self.api_url, json=payload)
        response.raise_for_status()
        raw_code = response.json().get("response", "")
        return raw_code.replace("```python", "").replace("```", "").strip()

    def write_code(self, task_description, filename):
        prompt = f"Tu es un développeur Python expert. Écris un script complet et fonctionnel pour la tâche suivante : {task_description}. Ne renvoie QUE le code pur, sans balises markdown."
        try:
            clean_code = self._call_llm(prompt, self.junior_model)
            filepath = os.path.join(self.workspace_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(clean_code)
            return {"status": "success", "file": filepath}
        except Exception as e:
            return {"status": "error", "details": str(e)}

    def execute_code(self, filename):
        filepath = os.path.join(self.workspace_dir, filename)
        if not os.path.exists(filepath):
            return {"status": "error", "details": f"Fichier {filename} introuvable."}
        try:
            result = subprocess.run(["python", filepath], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return {"status": "success", "output": result.stdout.strip()}
            else:
                return {"status": "failed", "error": result.stderr.strip()}
        except subprocess.TimeoutExpired:
            return {"status": "failed", "error": "Le script a mis trop de temps à s'exécuter (Timeout de 30s)."}
        except Exception as e:
            return {"status": "error", "details": str(e)}

    def auto_code_and_fix(self, task_description, filename, max_retries=2):
        write_res = self.write_code(task_description, filename)
        if write_res["status"] != "success":
            return write_res
        
        filepath = write_res["file"]
        
        for attempt in range(max_retries + 1):
            exec_res = self.execute_code(filename)
            
            if exec_res["status"] == "success":
                return {
                    "status": "success", 
                    "attempts": attempt + 1,
                    "final_model_used": self.junior_model if attempt == 0 else self.senior_model,
                    "output": exec_res["output"]
                }
            
            if attempt < max_retries:
                error_msg = exec_res.get("error", exec_res.get("details"))
                with open(filepath, "r", encoding="utf-8") as f:
                    current_code = f.read()
                    
                fix_prompt = f"Tâche originale: {task_description}\n\nCode actuel:\n{current_code}\n\nErreur lors de l'exécution:\n{error_msg}\n\nCorrige ce code. Ne renvoie QUE le code pur, sans markdown."
                
                try:
                    fixed_code = self._call_llm(fix_prompt, self.senior_model)
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(fixed_code)
                except Exception as e:
                    return {"status": "error", "details": f"Erreur de l'API LLM: {str(e)}"}
        
        return {"status": "failed", "details": "Échec après plusieurs tentatives.", "last_error": exec_res}
