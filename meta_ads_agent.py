"""
==============================================================
          META ADS AGENT - Powered by OpenAI
  Fale em português -> GPT pensa -> API do Meta executa
==============================================================
"""

import os, json, time, base64, mimetypes, requests, threading
from datetime import datetime, timedelta
from pathlib import Path
import openai
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# Servidor Web para Health Check (Necessário para Render/Nuvem)
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_web.run(host='0.0.0.0', port=port)

load_dotenv()

ACCESS_TOKEN    = os.getenv("META_ACCESS_TOKEN", "")
OPENAI_KEY      = os.getenv("OPENAI_API_KEY", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
BASE_URL        = "https://graph.facebook.com/v25.0"
SAVE_DIR        = Path("./dados_meta")
SAVE_DIR.mkdir(exist_ok=True)

# --- Configurações Comerciais (Isolamento e Limites) ---
ALLOWED_ACCOUNTS = os.getenv("ALLOWED_ACCOUNTS", "").split(",")
ALLOWED_ACCOUNTS = [a.strip() for a in ALLOWED_ACCOUNTS if a.strip()]
DAILY_MESSAGE_LIMIT = int(os.getenv("DAILY_MESSAGE_LIMIT", "100"))
CLIENT_NAME = os.getenv("CLIENT_NAME", "Usuário") # Nome amigável do cliente (ex: Mecorcamp)
CLIENT_ID = os.getenv("CLIENT_ID", "pessoal")    # Identificador interno
# -----------------------------------------------------

# --- Gerenciamento de Uso ---
def check_usage():
    if DAILY_MESSAGE_LIMIT <= 0: return True # Sem limite
    hoje = datetime.now().strftime("%Y-%m-%d")
    log_path = SAVE_DIR / "daily_usage.json"
    usage = {}
    if log_path.exists():
        with open(log_path, "r") as f: usage = json.load(f)
    
    current = usage.get(hoje, 0)
    if current >= DAILY_MESSAGE_LIMIT:
        return False
    
    usage[hoje] = current + 1
    with open(log_path, "w") as f: json.dump(usage, f)
    return True
# ----------------------------


class MetaAPI:
    def __init__(self, token):
        self.token = token
        self.session = requests.Session()
        self.session.params = {"access_token": token}

    def get(self, path, params=None):
        r = self.session.get(f"{BASE_URL}/{path.lstrip('/')}", params=params or {})
        return r.json()

    def post(self, path, data=None, files=None):
        if files:
            r = self.session.post(f"{BASE_URL}/{path.lstrip('/')}", data=data or {}, files=files)
        else:
            r = self.session.post(f"{BASE_URL}/{path.lstrip('/')}", json=data or {})
        return r.json()

    def delete(self, path):
        r = self.session.delete(f"{BASE_URL}/{path.lstrip('/')}")
        return r.json()


def descobrir_contas(api):
    print("\n🔍 Descobrindo contas conectadas ao token...")
    me = api.get("me", {"fields": "id,name,email"})
    
    # 1. Puxa as Ad Accounts diretas do User
    ad_accounts_raw = api.get("me/adaccounts", {
        "fields": "id,name,account_id,account_status,currency,timezone_name,daily_spend_limit,amount_spent,balance"
    })
    todas_ad_accounts = {}
    for acc in ad_accounts_raw.get("data", []):
        todas_ad_accounts[acc["id"]] = acc

    # 2. Puxa os BM's e as Ad Accounts de cada BM (Gera redundância útil e acha contas que não aparecem no /me/adaccounts dependendo da permissão)
    businesses = api.get("me/businesses", {"fields": "id,name"})
    for b in businesses.get("data", []):
        b_owned = api.get(f"{b['id']}/owned_ad_accounts", {"fields": "id,name,account_id,account_status,currency,timezone_name"})
        for acc in b_owned.get("data", []):
            todas_ad_accounts[acc["id"]] = acc
            
        b_client = api.get(f"{b['id']}/client_ad_accounts", {"fields": "id,name,account_id,account_status,currency,timezone_name"})
        for acc in b_client.get("data", []):
            todas_ad_accounts[acc["id"]] = acc

    pages_raw = api.get("me/accounts", {"fields": "id,name,access_token,category,instagram_business_account"})

    contas = {
        "usuario": me,
        "ad_accounts": list(todas_ad_accounts.values()),
        "paginas": pages_raw.get("data", []),
        "instagram_accounts": [],
        "descoberto_em": datetime.now().isoformat()
    }

    for page in contas["paginas"]:
        ig = page.get("instagram_business_account")
        if ig:
            ig_detail = api.get(ig["id"], {"fields": "id,username,name,followers_count,profile_picture_url"})
            contas["instagram_accounts"].append({**ig_detail, "pagina_id": page["id"], "pagina_nome": page["name"]})

    # --- Filtro de Isolamento de Cliente ---
    if ALLOWED_ACCOUNTS or (CLIENT_NAME and CLIENT_NAME != "Usuário"):
        # Filtrar Contas de Anúncios (Sempre por ID se fornecido)
        if ALLOWED_ACCOUNTS:
            contas["ad_accounts"] = [a for a in contas.get("ad_accounts", []) 
                                    if a["id"].replace("act_", "") in ALLOWED_ACCOUNTS or a["id"] in ALLOWED_ACCOUNTS]
        
        # Filtrar Páginas e IG (Pelo nome do cliente se não for 'Usuário')
        if CLIENT_NAME != "Usuário":
            contas["paginas"] = [p for p in contas.get("paginas", []) 
                                 if CLIENT_NAME.lower() in p["name"].lower()]
            contas["instagram_accounts"] = [ig for ig in contas.get("instagram_accounts", []) 
                                            if CLIENT_NAME.lower() in ig.get("username", "").lower()]

    save_json(contas, "contas_descobertas.json")
    print(f"  ✅ Usuário: {me.get('name')} (ID: {me.get('id')})")
    print(f"  ✅ Contas de anúncios filtradas: {len(contas['ad_accounts'])}")
    print(f"  ✅ Páginas Facebook filtradas: {len(contas['paginas'])}")
    print(f"  ✅ Contas Instagram filtradas: {len(contas['instagram_accounts'])}")
    return contas


def save_json(data, filename):
    path = SAVE_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  💾 Salvo: {path}")


TOOLS = [
    {"type": "function", "function": {"name": "listar_contas", "description": "Lista todas as contas de anúncios, páginas Facebook e contas Instagram conectadas ao token", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "listar_campanhas", "description": "Lista campanhas de uma conta de anúncios com status, objetivo e orçamento", "parameters": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "status": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id"]}}},
    {"type": "function", "function": {"name": "criar_campanha", "description": "Cria uma nova campanha no Meta Ads", "parameters": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "nome": {"type": "string"}, "objetivo": {"type": "string", "enum": ["OUTCOME_TRAFFIC","OUTCOME_AWARENESS","OUTCOME_LEADS","OUTCOME_SALES","OUTCOME_ENGAGEMENT","OUTCOME_APP_PROMOTION"]}, "orcamento_diario_centavos": {"type": "integer"}, "orcamento_total_centavos": {"type": "integer"}, "status": {"type": "string", "enum": ["ACTIVE","PAUSED"], "default": "PAUSED"}, "data_inicio": {"type": "string"}, "data_fim": {"type": "string"}}, "required": ["ad_account_id", "nome", "objetivo"]}}},
    {"type": "function", "function": {"name": "editar_campanha", "description": "Edita nome, status, orçamento ou datas de uma campanha existente", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "string"}, "nome": {"type": "string"}, "status": {"type": "string", "enum": ["ACTIVE","PAUSED","ARCHIVED","DELETED"]}, "orcamento_diario_centavos": {"type": "integer"}, "orcamento_total_centavos": {"type": "integer"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "deletar_campanha", "description": "Deleta permanentemente uma campanha", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "string"}}, "required": ["campaign_id"]}}},
    {"type": "function", "function": {"name": "listar_conjuntos", "description": "Lista conjuntos de anúncios (adsets) de uma campanha ou conta", "parameters": {"type": "object", "properties": {"campaign_id": {"type": "string"}, "ad_account_id": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "criar_conjunto", "description": "Cria um conjunto de anúncios com segmentação de público e interesses", "parameters": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "campaign_id": {"type": "string"}, "nome": {"type": "string"}, "orcamento_diario_centavos": {"type": "integer"}, "objetivo_otimizacao": {"type": "string", "enum": ["REACH","IMPRESSIONS","LINK_CLICKS","LANDING_PAGE_VIEWS","LEAD_GENERATION","CONVERSIONS","VIDEO_VIEWS"], "default": "LINK_CLICKS"}, "evento_cobranca": {"type": "string", "enum": ["IMPRESSIONS","LINK_CLICKS"], "default": "IMPRESSIONS"}, "paises": {"type": "array", "items": {"type": "string"}}, "idade_min": {"type": "integer", "default": 18}, "idade_max": {"type": "integer", "default": 65}, "generos": {"type": "array", "items": {"type": "integer"}}, "interesses_ids": {"type": "array", "items": {"type": "string"}}, "page_id": {"type": "string"}, "instagram_account_id": {"type": "string"}}, "required": ["ad_account_id", "campaign_id", "nome", "orcamento_diario_centavos"]}}},
    {"type": "function", "function": {"name": "editar_conjunto", "description": "Edita status, orçamento ou segmentação de um conjunto de anúncios", "parameters": {"type": "object", "properties": {"adset_id": {"type": "string"}, "nome": {"type": "string"}, "status": {"type": "string", "enum": ["ACTIVE","PAUSED","ARCHIVED","DELETED"]}, "orcamento_diario_centavos": {"type": "integer"}}, "required": ["adset_id"]}}},
    {"type": "function", "function": {"name": "fazer_upload_imagem", "description": "Faz upload de uma imagem para a conta de anúncios e retorna o hash", "parameters": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "caminho_imagem": {"type": "string"}}, "required": ["ad_account_id", "caminho_imagem"]}}},
    {"type": "function", "function": {"name": "fazer_upload_video", "description": "Faz upload de um vídeo para a conta de anúncios e retorna o video_id", "parameters": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "caminho_video": {"type": "string"}, "nome": {"type": "string"}}, "required": ["ad_account_id", "caminho_video"]}}},
    {"type": "function", "function": {"name": "criar_criativo", "description": "Cria um criativo de anúncio (imagem ou vídeo + texto + link)", "parameters": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "page_id": {"type": "string"}, "titulo": {"type": "string"}, "corpo": {"type": "string"}, "descricao": {"type": "string"}, "url_destino": {"type": "string"}, "call_to_action": {"type": "string", "enum": ["LEARN_MORE","SHOP_NOW","SIGN_UP","CONTACT_US","GET_QUOTE","BOOK_NOW","DOWNLOAD","WATCH_MORE","APPLY_NOW","GET_OFFER"], "default": "LEARN_MORE"}, "image_hash": {"type": "string"}, "video_id": {"type": "string"}, "instagram_account_id": {"type": "string"}}, "required": ["ad_account_id", "page_id", "titulo", "corpo", "url_destino"]}}},
    {"type": "function", "function": {"name": "buscar_interesses", "description": "Busca IDs de interesses no Meta para segmentação (ex: 'Investimentos')", "parameters": {"type": "object", "properties": {"termo": {"type": "string"}}, "required": ["termo"]}}},
    {"type": "function", "function": {"name": "duplicar_conjuntos", "description": "Duplica um conjunto de anúncios múltiplas vezes com um novo orçamento", "parameters": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "adset_id": {"type": "string"}, "quantidade": {"type": "integer"}, "novo_orcamento_diario_centavos": {"type": "integer"}}, "required": ["ad_account_id", "adset_id", "quantidade"]}}},
    {"type": "function", "function": {"name": "obter_insights", "description": "Obtém métricas de performance (CTR, CPC, Conversões) para análise estratégica", "parameters": {"type": "object", "properties": {"object_id": {"type": "string"}, "periodo": {"type": "string", "enum": ["hoje","ontem","7dias","14dias","30dias","mes_atual"], "default": "mes_atual"}}, "required": ["object_id"]}}},
    {"type": "function", "function": {"name": "pesquisar_biblioteca_anuncios", "description": "Busca anúncios na Biblioteca Pública do Meta para espionar concorrência", "parameters": {"type": "object", "properties": {"search_terms": {"type": "string"}, "country": {"type": "string", "default": "BR"}}, "required": ["search_terms"]}}},
]


class ToolExecutor:
    def __init__(self, api, contas):
        self.api = api
        self.contas = contas

    def run(self, name, inp):
        print(f"\n  🔧 [{name}] {json.dumps(inp, ensure_ascii=False)[:120]}")
        try:
            result = getattr(self, f"_ToolExecutor__{name}")(inp)
        except AttributeError:
            result = {"erro": f"Tool '{name}' não implementada"}
        except Exception as e:
            result = {"erro": str(e)}
        save_json(result, f"tool_{name}_{int(time.time())}.json")
        return json.dumps(result, ensure_ascii=False, default=str)

    def __listar_contas(self, _): 
        if not ALLOWED_ACCOUNTS: return self.contas
        
        # Filtrar Contas de Anúncios
        ad_filtradas = [a for a in self.contas.get("ad_accounts", []) 
                        if a["id"].replace("act_", "") in ALLOWED_ACCOUNTS or a["id"] in ALLOWED_ACCOUNTS]
        
        # Filtrar Páginas e Instagram (Baseado no nome do cliente para ser mais inteligente)
        # Se o CLIENT_NAME estiver em 'Mecorcamp', só puxa o que tiver esse nome
        paginas_filtradas = [p for p in self.contas.get("paginas", []) 
                             if CLIENT_NAME.lower() in p["name"].lower()] if CLIENT_NAME != "Usuário" else self.contas.get("paginas", [])
        
        ig_filtradas = [ig for ig in self.contas.get("instagram_accounts", []) 
                        if CLIENT_NAME.lower() in ig.get("username", "").lower()] if CLIENT_NAME != "Usuário" else self.contas.get("instagram_accounts", [])

        return {
            "ad_accounts": ad_filtradas,
            "paginas": paginas_filtradas,
            "instagram_accounts": ig_filtradas
        }

    def __listar_campanhas(self, inp):
        params = {"fields": "id,name,status,objective,daily_budget,lifetime_budget,start_time,stop_time,created_time,spend_cap"}
        if inp.get("status"): params["effective_status"] = json.dumps(inp["status"])
        return self.api.get(f"{inp['ad_account_id']}/campaigns", params)

    def __criar_campanha(self, inp):
        data = {"name": inp["nome"], "objective": inp["objetivo"], "status": inp.get("status", "PAUSED"), "special_ad_categories": []}
        if inp.get("orcamento_diario_centavos"): data["daily_budget"] = str(inp["orcamento_diario_centavos"])
        if inp.get("orcamento_total_centavos"): data["lifetime_budget"] = str(inp["orcamento_total_centavos"])
        if inp.get("data_inicio"): data["start_time"] = inp["data_inicio"] + "T00:00:00-0300"
        if inp.get("data_fim"): data["stop_time"] = inp["data_fim"] + "T23:59:59-0300"
        return self.api.post(f"{inp['ad_account_id']}/campaigns", data)

    def __editar_campanha(self, inp):
        cid = inp.pop("campaign_id"); data = {}
        if inp.get("nome"): data["name"] = inp["nome"]
        if inp.get("status"): data["status"] = inp["status"]
        if inp.get("orcamento_diario_centavos"): data["daily_budget"] = str(inp["orcamento_diario_centavos"])
        if inp.get("orcamento_total_centavos"): data["lifetime_budget"] = str(inp["orcamento_total_centavos"])
        return self.api.post(cid, data)

    def __deletar_campanha(self, inp): return self.api.delete(inp["campaign_id"])

    def __listar_conjuntos(self, inp):
        fields = "id,name,status,daily_budget,lifetime_budget,targeting,optimization_goal,billing_event,campaign_id"
        if inp.get("campaign_id"): return self.api.get(f"{inp['campaign_id']}/adsets", {"fields": fields})
        return self.api.get(f"{inp['ad_account_id']}/adsets", {"fields": fields})

    def __criar_conjunto(self, inp):
        targeting = {"geo_locations": {"countries": inp.get("paises", ["BR"])}, "age_min": inp.get("idade_min", 18), "age_max": inp.get("idade_max", 65)}
        if inp.get("generos"): targeting["genders"] = inp["generos"]
        if inp.get("interesses_ids"): 
            targeting["flexible_spec"] = [{"interests": [{"id": i} for i in inp["interesses_ids"]]}]
            
        data = {"name": inp["nome"], "campaign_id": inp["campaign_id"], "daily_budget": str(inp["orcamento_diario_centavos"]), "optimization_goal": inp.get("objetivo_otimizacao", "LINK_CLICKS"), "billing_event": inp.get("evento_cobranca", "IMPRESSIONS"), "targeting": json.dumps(targeting), "status": "PAUSED", "start_time": datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + "-0300"}
        if inp.get("page_id"): data["promoted_object"] = json.dumps({"page_id": inp["page_id"]})
        return self.api.post(f"{inp['ad_account_id']}/adsets", data)

    def __buscar_interesses(self, inp):
        return self.api.get("search", {"type": "adinterest", "q": inp["termo"]})

    def __duplicar_conjuntos(self, inp):
        # 1. Obter o conjunto original
        orig = self.api.get(inp["adset_id"], {"fields": "name,campaign_id,targeting,optimization_goal,billing_event,promoted_object"})
        if "id" not in orig: return {"erro": "Conjunto original não encontrado"}
        
        # 2. Obter os anúncios do conjunto original para replicar os criativos
        ads = self.api.get(f"{inp['adset_id']}/ads", {"fields": "name,creative"})
        
        results = []
        for i in range(inp["quantidade"]):
            # Criar novo conjunto
            new_data = {
                "name": f"{orig['name']} (Duplicado {i+1})",
                "campaign_id": orig["campaign_id"],
                "daily_budget": str(inp.get("novo_orcamento_diario_centavos", 1000)), # Default R$10 se não informado
                "targeting": json.dumps(orig["targeting"]),
                "optimization_goal": orig["optimization_goal"],
                "billing_event": orig["billing_event"],
                "status": "PAUSED"
            }
            if "promoted_object" in orig: new_data["promoted_object"] = json.dumps(orig["promoted_object"])
            
            new_adset = self.api.post(f"{inp['ad_account_id']}/adsets", new_data)
            
            # Replicar anúncios no novo conjunto
            if "id" in new_adset:
                for ad in ads.get("data", []):
                    self.api.post(f"{inp['ad_account_id']}/ads", {
                        "name": ad["name"],
                        "adset_id": new_adset["id"],
                        "creative": json.dumps({"creative_id": ad["creative"]["id"]}),
                        "status": "PAUSED"
                    })
            results.append(new_adset.get("id", "erro"))
            
        return {"ids_criados": results, "mensagem": f"{len(results)} conjuntos duplicados com sucesso."}

    def __pesquisar_biblioteca_anuncios(self, inp):
        params = {"search_terms": inp["search_terms"], "ad_reached_countries": [inp.get("country", "BR")], "ad_type": "ALL", "ad_active_status": "ACTIVE", "fields": "ad_creative_bodies,ad_creative_link_captions,publisher_platforms", "limit": 10}
        return self.api.get("ads_archive", params)

    def __editar_conjunto(self, inp):
        aid = inp.pop("adset_id"); data = {}
        if inp.get("nome"): data["name"] = inp["nome"]
        if inp.get("status"): data["status"] = inp["status"]
        if inp.get("orcamento_diario_centavos"): data["daily_budget"] = str(inp["orcamento_diario_centavos"])
        return self.api.post(aid, data)

    def __fazer_upload_imagem(self, inp):
        path = inp["caminho_imagem"]
        if not os.path.exists(path): return {"erro": f"Arquivo não encontrado: {path}"}
        with open(path, "rb") as f:
            files = {"filename": (os.path.basename(path), f, mimetypes.guess_type(path)[0] or "image/jpeg")}
            return self.api.post(f"{inp['ad_account_id']}/adimages", {}, files=files)

    def __fazer_upload_video(self, inp):
        path = inp["caminho_video"]
        if not os.path.exists(path): return {"erro": f"Arquivo não encontrado: {path}"}
        # Upload de vídeo no Meta usa um endpoint diferente e multipart
        with open(path, "rb") as f:
            data = {"name": inp.get("nome", os.path.basename(path))}
            files = {"source": (os.path.basename(path), f, "video/mp4")}
            return self.api.post(f"{inp['ad_account_id']}/advideos", data, files=files)

    def __criar_criativo(self, inp):
        link_data = {"message": inp["corpo"], "link": inp["url_destino"], "name": inp["titulo"], "call_to_action": {"type": inp.get("call_to_action", "LEARN_MORE")}}
        if inp.get("descricao"): link_data["description"] = inp["descricao"]
        
        story_spec = {"page_id": inp["page_id"]}
        if inp.get("instagram_account_id"): story_spec["instagram_actor_id"] = inp["instagram_account_id"]

        if inp.get("video_id"):
            # Estrutura para vídeo
            story_spec["video_data"] = {
                "video_id": inp["video_id"],
                "message": inp["corpo"],
                "call_to_action": {"type": inp.get("call_to_action", "LEARN_MORE"), "value": {"link": inp["url_destino"]}},
                "title": inp["titulo"]
            }
        else:
            # Estrutura para imagem
            if inp.get("image_hash"): link_data["image_hash"] = inp["image_hash"]
            story_spec["link_data"] = link_data

        return self.api.post(f"{inp['ad_account_id']}/adcreatives", {"name": f"Criativo - {inp['titulo'][:40]}", "object_story_spec": json.dumps(story_spec)})

    def __listar_anuncios(self, inp):
        fields = "id,name,status,creative,adset_id,campaign_id,created_time"
        if inp.get("adset_id"): return self.api.get(f"{inp['adset_id']}/ads", {"fields": fields})
        if inp.get("campaign_id"): return self.api.get(f"{inp['campaign_id']}/ads", {"fields": fields})
        return self.api.get(f"{inp['ad_account_id']}/ads", {"fields": fields})

    def __criar_anuncio(self, inp):
        return self.api.post(f"{inp['ad_account_id']}/ads", {"name": inp["nome"], "adset_id": inp["adset_id"], "creative": json.dumps({"creative_id": inp["creative_id"]}), "status": inp.get("status", "PAUSED")})

    def __editar_anuncio(self, inp):
        aid = inp.pop("ad_id"); data = {}
        if inp.get("nome"): data["name"] = inp["nome"]
        if inp.get("status"): data["status"] = inp["status"]
        return self.api.post(aid, data)

    def __obter_insights(self, inp):
        hoje = datetime.now().strftime("%Y-%m-%d")
        ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        periodos = {"hoje": (hoje, hoje), "ontem": (ontem, ontem), "7dias": ((datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d"), hoje), "14dias": ((datetime.now()-timedelta(days=14)).strftime("%Y-%m-%d"), hoje), "30dias": ((datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d"), hoje), "mes_atual": (datetime.now().replace(day=1).strftime("%Y-%m-%d"), hoje), "personalizado": (inp.get("data_inicio", hoje), inp.get("data_fim", hoje))}
        inicio, fim = periodos[inp.get("periodo", "7dias")]
        fields = "campaign_name,adset_name,ad_name,impressions,reach,frequency,clicks,unique_clicks,ctr,cpc,cpm,spend,actions,cost_per_action_type,video_thruplay_watched_actions,video_play_actions"
        params = {"fields": fields, "time_range": json.dumps({"since": inicio, "until": fim}), "level": inp.get("nivel", "campaign")}
        if inp.get("breakdowns"): params["breakdowns"] = ",".join(inp["breakdowns"])
        return self.api.get(f"{inp['object_id']}/insights", params)

    def __gerar_relatorio(self, inp):
        hoje = datetime.now().strftime("%Y-%m-%d")
        periodos = {"7dias": (datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d"), "30dias": (datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d"), "mes_atual": datetime.now().replace(day=1).strftime("%Y-%m-%d")}
        inicio = periodos[inp.get("periodo", "7dias")]; acc_id = inp["ad_account_id"]
        campanhas = self.api.get(f"{acc_id}/campaigns", {"fields": "id,name,status,objective,daily_budget"})
        insights = self.api.get(f"{acc_id}/insights", {"fields": "campaign_name,impressions,reach,clicks,ctr,cpc,cpm,spend,actions", "time_range": json.dumps({"since": inicio, "until": hoje}), "level": "campaign"})
        adsets = self.api.get(f"{acc_id}/adsets", {"fields": "id,name,status,daily_budget,campaign_id"})
        relatorio = {"conta": acc_id, "periodo": f"{inicio} a {hoje}", "campanhas": campanhas.get("data", []), "insights_por_campanha": insights.get("data", []), "conjuntos": adsets.get("data", []), "gerado_em": datetime.now().isoformat()}
        if inp.get("salvar_arquivo", True): save_json(relatorio, f"relatorio_{acc_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
        return relatorio

    def __instagram_insights(self, inp):
        ig_id = inp["instagram_account_id"]; periodo = inp.get("periodo", "day")
        # 1. Obter Perfil
        try:
            perfil = self.api.get(ig_id, {"fields": "id,username,name,biography,followers_count,media_count"})
        except Exception as e:
            perfil = {"erro": str(e)}

        # 2. Obter Insights (Alguns tokens podem não ter as permissões necessárias para ler insights)
        try:
            insights = self.api.get(f"{ig_id}/insights", {"metric": "impressions,reach,profile_views", "period": periodo})
        except Exception as e:
            insights = {"erro": "Sem permissão ou métrica indisponível. Detalhes: " + str(e)}
            
        return {"perfil": perfil, "insights": insights.get("data", insights)}

class MetaAdsAgent:
    def __init__(self):
        self.api = MetaAPI(ACCESS_TOKEN)
        self.contas = descobrir_contas(self.api)
        self.executor = ToolExecutor(self.api, self.contas)
        self.openai_client = openai.Client(api_key=OPENAI_KEY)
        self.historico = []
        self._carregar_historico()
        self.system_prompt = self._build_system_prompt()
        if not self.historico or self.historico[0].get("role") != "system":
             self.historico.insert(0, {"role": "system", "content": self.system_prompt})
        else:
             self.historico[0]["content"] = self.system_prompt

    def _salvar_historico(self):
        with open(SAVE_DIR / "chat_history.json", "w", encoding="utf-8") as f:
            # We can't easily serialize Pydantic objects from OpenAI, so we save basic dicts
            clean_history = []
            for m in self.historico:
                if isinstance(m, dict):
                    clean_history.append(m)
                else:
                    # It's an OpenAI message object
                    d = {"role": m.role, "content": m.content}
                    if hasattr(m, "tool_calls") and m.tool_calls:
                        d["tool_calls"] = [{"id": t.id, "type": t.type, "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in m.tool_calls]
                    if hasattr(m, "tool_call_id"):
                        d["tool_call_id"] = m.tool_call_id
                        d["name"] = m.name
                    clean_history.append(d)
            json.dump(clean_history, f, ensure_ascii=False, indent=2)

    def _carregar_historico(self):
        try:
            with open(SAVE_DIR / "chat_history.json", "r", encoding="utf-8") as f:
                self.historico = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.historico = []

    def _build_system_prompt(self):
        usuario = self.contas.get("usuario", {})
        ad_accounts = self.contas.get("ad_accounts", [])
        paginas = self.contas.get("paginas", [])
        ig_accounts = self.contas.get("instagram_accounts", [])
        contas_str = "\n"
        global_idx = 1
        
        if ad_accounts:
            for a in ad_accounts:
                status = "✅ ATIVA" if a.get("account_status") == 1 else "⚠️ " + str(a.get("account_status"))
                contas_str += f"{global_idx}. Conta de Anúncios | {a.get('name')} | ID: {a.get('id')} | {status} | {a.get('currency','BRL')}\n"
                global_idx += 1
        if paginas:
            for p in paginas: 
                contas_str += f"{global_idx}. Página Facebook | {p.get('name')} | ID: {p.get('id')}\n"
                global_idx += 1
        if ig_accounts:
            for ig in ig_accounts: 
                contas_str += f"{global_idx}. Conta Instagram | @{ig.get('username','?')} | ID: {ig.get('id')} | Página: {ig.get('pagina_nome','?')}\n"
                global_idx += 1
        
        return f"""Você é um especialista em Meta Ads com acesso total à conta via API.

CONTA CONECTADA:
👤 Usuário: {usuario.get('name','?')} (ID: {usuario.get('id','?')})
{contas_str}

SUAS CAPACIDADES: criar/editar/pausar/ativar/deletar campanhas, conjuntos de anúncios, criativos e anúncios para Facebook e Instagram. Obter métricas (CTR, CPC, CPM, conversões). Gerar relatórios em JSON.

Se não houver Contas de Anúncios na listagem acima (ad_accounts estiver vazio), VOCÊ AINDA FUNCIONA. Apenas informe o usuário cordialmente que não detectou contas de anúncios vinculadas diretamente, e pergunte se ele tem o ID da Conta de Anúncios (ad_account_id) para você tentar consultar manualmente (formato act_123456). VOCÊ NÃO DEVE listar criar campanhas ou anúncios nesses menus sem ter o act_id.
Você tem a capacidade de postar diretamente pelo Instagram (`instagram_account_id`), mas para subir anúncios/campanhas é NECESSÁRIO o `ad_account_id`.

REGRAS CRÍTICAS DE EXPERIÊNCIA DO USUÁRIO (Obrigatório seguir):
1. NUNCA USE FORMATO DE TABELAS (ASCII PIPES | ). Em vez disso, SEMPRE USE LISTAS NUMERADAS VERTICAIS. O Telegram no celular quebra tabelas.
2. É OBRIGATÓRIO inserir uma linha em branco (usar `\\n\\n` dupla quebra de linha) entre CADA item numérico de uma lista. Nunca concatene opções no mesmo parágrafo. A leitura deve ser espaçada. Exemplo de formatação obrigatória:
1. Opção A

2. Opção B

3. Opção C
3. Sempre que listar Campanhas, Conjuntos ou Anúncios para escolha, coloque um número sequencial na frente (1., 2., 3....) para que o usuário possa responder apenas com o número. Entenda quando o usuário responder apenas um número e relacione com a última lista gerada para saber qual ID ele selecionou.
4. Não peça confirmação passo a passo se o usuário já forneceu todas as informações necessárias na primeira mensagem. (Ex: "Crie um anúncio com esse texto e imagem" -> Se tem texto e a foto, execute a action de criar e depois criar_anuncio).
5. Campanhas novas sempre nascem PAUSED. Antes de de deletar, confirme. Orçamentos em centavos (R$50=5000). Responda sempre de forma enumerada e espaçada.
6. SEMPRE TENTE TRAZER O MAXIMO DE CAMPANHAS POSSIVES PARA A LISTAGEM PARA ENCONTRAR A CAMPANHA CORRETA, NUNCA TRUNQUE PARA TOP 5 OU TOP 10. Traga todas que tiverem o status ou tudo se não tiver nada filtrando.
7. REGRAS PARA CLIENTES LEIGOS (Mecorcamp):
   - Se houver apenas 1 Conta de Anúncios na listagem acima, considere-a SELECIONADA AUTOMATICAMENTE. Nunca pergunte qual conta ou peça o ID se houver apenas uma opção.
   - REAÇÃO A NÚMEROS: Se o usuário digitar apenas um número (ex: "1"), verifique qual era a opção correspondente no último menu e EXECUTE a ferramenta necessária IMEDIATAMENTE.
   - Por exemplo: Se o usuário digitar "1" no menu inicial e houver apenas 1 conta, você deve chamar a ferramenta `obter_insights` para essa conta no período `mes_atual` na hora, sem perguntar mais nada.

   - Siga esta ordem de períodos para sugerir ou usar: 1. Hoje | 2. Ontem | 3. Mês Atual | 4. Últimos 7 dias. Se o usuário disser apenas "analisar", traga o "Mês Atual" por padrão mas pergunte se ele quer ver outro período.

HOJE: {datetime.now().strftime('%d/%m/%Y %H:%M')}"""

    def processar(self, mensagem):
        self.historico.append({"role": "user", "content": mensagem})
        self._salvar_historico()
        
        while True:
            resp = self.openai_client.chat.completions.create(
                model="gpt-4o", 
                messages=self.historico,
                tools=TOOLS,
                tool_choice="auto"
            )
            msg = resp.choices[0].message
            
            if msg.tool_calls:
                self.historico.append(msg)
                self._salvar_historico()
                for tool_call in msg.tool_calls:
                    function_name = tool_call.function.name
                    import json
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                    
                    resultado = self.executor.run(function_name, arguments)
                    
                    self.historico.append({
                        "role": "tool", 
                        "tool_call_id": tool_call.id, 
                        "name": function_name, 
                        "content": resultado
                    })
                    self._salvar_historico()
            else:
                texto = msg.content
                self.historico.append({"role": "assistant", "content": texto})
                self._salvar_historico()
                return texto


agent_instance = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Reset conversation when /start is hit
    if agent_instance:
         agent_instance.contas = descobrir_contas(agent_instance.api)
         agent_instance.system_prompt = agent_instance._build_system_prompt()
         agent_instance.historico = []
         agent_instance.historico.append({"role": "system", "content": agent_instance.system_prompt})
         agent_instance._salvar_historico()
         
    # Gerar a saudação e lista de estrutura (Simplificado para cliente único)
    ad_accounts = agent_instance.contas.get("ad_accounts", [])
    if len(ad_accounts) == 1:
        contas_info = f"🎯 Conectado à conta: **{ad_accounts[0].get('name')}**"
    else:
        contas_info = "Sua Estrutura no Meta:\n"
        global_idx = 1
        for a in ad_accounts:
            contas_info += f"\n{global_idx}. Conta de Anúncios | {a.get('name')}"
            global_idx += 1
        for p in agent_instance.contas.get("paginas", []):
            contas_info += f"\n{global_idx}. Página Facebook | {p.get('name')}"
            global_idx += 1
        for ig in agent_instance.contas.get("instagram_accounts", []):
            contas_info += f"\n{global_idx}. Conta Instagram | @{ig.get('username','?')}"
            global_idx += 1

    welcome_text = (
        f"🚀 **Olá, {CLIENT_NAME}!**\n\n"
        "Sou seu consultor de tráfego inteligente. Já analisei sua estrutura e estou pronto para gerenciar seus anúncios!\n\n"
        f"{contas_info}\n\n"
        "**O que deseja fazer agora?**\n\n"
        "1. Analisar o desempenho das campanhas atuais\n\n"
        "2. Criar um novo anúncio ou escala em lote\n\n"
        "3. Espionar a concorrência na biblioteca do Meta\n\n"
        "Basta me enviar o número da opção ou me dizer o que precisa!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text
    msg = await update.message.reply_text("⏳ Puxando os dados no Meta e pensando...")
    if not check_usage():
        await msg.edit_text(f"⚠️ Limite diário de {DAILY_MESSAGE_LIMIT} mensagens atingido para este robô. Tente novamente amanhã!")
        return

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        resposta = await loop.run_in_executor(None, agent_instance.processar, user_text)
        await msg.edit_text(resposta)
    except Exception as e:
        await msg.edit_text(f"❌ Ocorreu um erro: {str(e)}")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # A media sent! Need to download it securely
    upload_dir = SAVE_DIR / "uploads"
    upload_dir.mkdir(exist_ok=True)
    
    msg_caption = update.message.caption or "Aqui está a mídia enviada."
    file_type = "arquivo descnhecido"
    
    if update.message.photo:
         media_file = await update.message.photo[-1].get_file()
         file_path = upload_dir / f"img_{int(time.time())}.jpg"
         file_type = "foto"
    elif update.message.video:
         media_file = await update.message.video.get_file()
         file_path = upload_dir / f"vid_{int(time.time())}.mp4"
         file_type = "vídeo"
    elif update.message.document:
         media_file = await update.message.document.get_file()
         file_path = upload_dir / f"doc_{int(time.time())}.{update.message.document.file_name.split('.')[-1]}"
         file_type = "documento"
    else:
         msg = await update.message.reply_text("❌ Tipo de arquivo não suportado.")
         return
         
    await media_file.download_to_drive(file_path)
    
    context_msg = f"[SISTEMA: O Usuário enviou um(a) {file_type}. O caminho absoluto deste arquivo no sistema local é: {file_path.absolute()} . Use este caminho quando precisar do criativo.]\n\nUsuário diz: {msg_caption}"
    
    msg = await update.message.reply_text(f"⏳ Recebi o(a) {file_type}! Pensando no próximo passo...")
    if not check_usage():
        await msg.edit_text(f"⚠️ Limite diário de {DAILY_MESSAGE_LIMIT} mensagens atingido. Tente novamente amanhã!")
        return

    try:
        import asyncio
        loop = asyncio.get_running_loop()
        resposta = await loop.run_in_executor(None, agent_instance.processar, context_msg)
        await msg.edit_text(resposta)
    except Exception as e:
        await msg.edit_text(f"❌ Ocorreu um erro: {str(e)}")

def main():
    print("\n" + "="*62)
    print("  LOG: META ADS AGENT - Powered by OpenAI & Telegram")
    print("="*62)
    
    if not OPENAI_KEY:
        print("\\n⚠️  OPENAI_API_KEY não configurada! Edite o arquivo .env")
        return
    if not TELEGRAM_TOKEN:
        print("\\n⚠️  TELEGRAM_TOKEN não configurado! Edite o arquivo .env")
        return

    global agent_instance
    agent_instance = MetaAdsAgent()
    print("\\n✅ Agente conectado ao Meta! Iniciando o bot do Telegram...")
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media))
    
    print("🤖 Bot do Telegram rodando. Mande mensagem lá!")
    
    # Inicia o Flask em uma thread separada
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    app.run_polling()

if __name__ == "__main__":
    main()
