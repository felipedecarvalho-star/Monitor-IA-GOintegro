#!/usr/bin/env python3
"""Monitor de visibilidade em respostas de IA.

Para cada prompt ativo da aba "prompts", pergunta ao ChatGPT e ao Gemini (ambos com busca na web),
extrai os fornecedores na ordem em que aparecem na resposta, coleta os links pesquisados e citados
e classifica cada link por categoria. Tudo é gravado em abas do Google Sheets.

Variáveis de ambiente obrigatórias:
  OPENAI_API_KEY, GEMINI_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON, SHEET_ID
Opcionais:
  OPENAI_MODEL, GEMINI_MODEL, EXTRACT_MODEL, LIMITE (testar só os N primeiros prompts)
"""
import argparse
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from google import genai
from google.genai import types
from openai import OpenAI

from setup_sheet import CATEGORIAS, abrir_planilha, ensure_tabs

TZ = ZoneInfo("America/Sao_Paulo")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "gpt-4o-mini")
MOTORES = ("ChatGPT", "Gemini")
LIMITE_CELULA = 45000  # o Google Sheets aceita no máximo 50.000 caracteres por célula

openai_client = OpenAI()
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


# ---------- utilidades de URL ----------

def limpar_url(url):
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urlunparse(p._replace(query=urlencode(q), fragment=""))


def dominio_de(url):
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def buscar_categoria(dominio, tabela):
    """Procura o domínio e, se não achar, os domínios-pai (blog.x.com -> x.com)."""
    partes = dominio.split(".")
    for i in range(len(partes) - 1):
        candidato = ".".join(partes[i:])
        if candidato in tabela:
            return tabela[candidato]
    return None


def resolver_redirecionamento(url):
    """O Gemini devolve links de redirecionamento do Google; seguimos até o destino real."""
    if "vertexaisearch.cloud.google.com" not in url:
        return limpar_url(url)
    try:
        r = requests.get(url, allow_redirects=True, timeout=10, stream=True)
        destino = r.url
        r.close()
        return limpar_url(destino)
    except requests.RequestException:
        return url


def montar_links(pesquisados, citados):
    """Une as duas listas mantendo a ordem de cada uma (sem repetir URLs)."""
    links = {}
    for i, s in enumerate(pesquisados, 1):
        links.setdefault(s["url"], {**s, "ordem_pesquisa": i, "ordem_citacao": ""})
    for j, c in enumerate(citados, 1):
        if c["url"] in links:
            if links[c["url"]]["ordem_citacao"] == "":
                links[c["url"]]["ordem_citacao"] = j
        else:
            links[c["url"]] = {**c, "ordem_pesquisa": "", "ordem_citacao": j}
    return list(links.values())


# ---------- consultas aos motores ----------

def com_nova_tentativa(funcao, tentativas=2, espera=5):
    for n in range(tentativas):
        try:
            return funcao()
        except Exception:
            if n == tentativas - 1:
                raise
            time.sleep(espera)


def perguntar_chatgpt(prompt):
    resp = openai_client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        tools=[{"type": "web_search"}],
        include=["web_search_call.action.sources"],
    )
    dados = resp.model_dump()
    pesquisados, citados = [], []
    for item in dados.get("output", []):
        if item.get("type") == "web_search_call":
            for s in (item.get("action") or {}).get("sources") or []:
                if s.get("url"):
                    pesquisados.append({"url": limpar_url(s["url"]), "title": s.get("title") or ""})
        elif item.get("type") == "message":
            for parte in item.get("content", []):
                for a in parte.get("annotations") or []:
                    if a.get("type") == "url_citation" and a.get("url"):
                        citados.append({"url": limpar_url(a["url"]), "title": a.get("title") or ""})
    return resp.output_text or "", pesquisados, citados


def perguntar_gemini(prompt):
    resp = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        ),
    )
    try:
        texto = resp.text or ""
    except Exception:
        texto = ""
    pesquisados, citados = [], []
    candidatos = resp.candidates or []
    meta = candidatos[0].grounding_metadata if candidatos else None
    if meta:
        blocos = meta.grounding_chunks or []
        for b in blocos:
            if b.web and b.web.uri:
                destino = resolver_redirecionamento(b.web.uri)
                pesquisados.append({"url": destino, "title": b.web.title or ""})
            else:
                pesquisados.append(None)
        usados = []
        for suporte in meta.grounding_supports or []:
            for idx in suporte.grounding_chunk_indices or []:
                if idx not in usados:
                    usados.append(idx)
        citados = [pesquisados[i] for i in usados if i < len(pesquisados) and pesquisados[i]]
        pesquisados = [p for p in pesquisados if p]
    return texto, pesquisados, citados


def extrair_fornecedores(texto):
    if not texto.strip():
        return []
    resp = openai_client.chat.completions.create(
        model=EXTRACT_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Você extrai nomes de fornecedores de software. Leia a resposta e liste, na ordem "
                    "exata em que aparecem pela primeira vez no texto, as plataformas/fornecedores "
                    "apresentados como opção ou recomendação. Use o nome comercial canônico (ex.: "
                    "'Workvivo', não 'Workvivo da Zoom'), sem duplicatas. Ignore ferramentas genéricas "
                    "(Excel, e-mail) e empresas citadas só como exemplo de cliente. "
                    'Responda apenas JSON: {"fornecedores": ["...", "..."]}'
                ),
            },
            {"role": "user", "content": texto},
        ],
    )
    dados = json.loads(resp.choices[0].message.content)
    vistos, lista = set(), []
    for nome in dados.get("fornecedores", []):
        chave = str(nome).strip().lower()
        if chave and chave not in vistos:
            vistos.add(chave)
            lista.append(str(nome).strip())
    return lista


def classificar_dominios(desconhecidos):
    """desconhecidos: {dominio: titulo_de_exemplo} -> {dominio: categoria}"""
    resultado = {}
    itens = list(desconhecidos.items())
    for i in range(0, len(itens), 40):
        lote = dict(itens[i:i + 40])
        resp = openai_client.chat.completions.create(
            model=EXTRACT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classifique cada domínio em exatamente uma categoria:\n"
                        "- Site oficial do fornecedor: site de uma plataforma de RH/comunicação/benefícios (páginas de produto, preços)\n"
                        "- Blog de fornecedor/SaaS: blog de conteúdo de uma empresa de software\n"
                        "- Hub de comparação e reviews: G2, Capterra e similares que listam e avaliam softwares\n"
                        "- Grande portal: veículo de notícias/negócios de grande audiência\n"
                        "- Imprensa especializada: veículo focado em RH, tecnologia ou gestão de pessoas\n"
                        "- Post compartilhado/rede social: LinkedIn, Reddit, YouTube, Medium, X, Facebook, Instagram\n"
                        "- Portal menor/hub de conteúdo: portais e blogs de menor porte, agregadores, diretórios\n"
                        "- Outros: acadêmico, governo, fóruns, o que não se encaixa\n"
                        "Entrada: JSON {domínio: título de exemplo}. Responda apenas JSON {domínio: categoria}, "
                        "usando exatamente os nomes de categoria acima."
                    ),
                },
                {"role": "user", "content": json.dumps(lote, ensure_ascii=False)},
            ],
        )
        dados = json.loads(resp.choices[0].message.content)
        for dominio in lote:
            cat = dados.get(dominio)
            resultado[dominio] = cat if cat in CATEGORIAS else "Outros"
    return resultado


# ---------- execução ----------

def rodar_job(prompt, motor):
    agora = datetime.now(TZ).isoformat(timespec="seconds")
    resultado = {
        "execucao_id": uuid.uuid4().hex[:12],
        "data_hora": agora,
        "prompt_id": prompt["id"],
        "motor": motor,
        "modelo": OPENAI_MODEL if motor == "ChatGPT" else GEMINI_MODEL,
        "status": "ok",
        "texto": "",
        "links": [],
        "fornecedores": [],
    }
    try:
        consulta = perguntar_chatgpt if motor == "ChatGPT" else perguntar_gemini
        texto, pesquisados, citados = com_nova_tentativa(lambda: consulta(prompt["prompt"]))
        resultado["texto"] = texto
        resultado["links"] = montar_links(pesquisados, citados)
        resultado["fornecedores"] = com_nova_tentativa(lambda: extrair_fornecedores(texto))
    except Exception as e:  # registramos o erro e seguimos com os demais
        resultado["status"] = f"erro: {type(e).__name__}: {str(e)[:200]}"
    return resultado


def ativo(valor):
    return str(valor).strip().lower() not in {"não", "nao", "n", "false", "0", "off"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=int(os.getenv("LIMITE") or 0),
                    help="rodar só os N primeiros prompts (para testar)")
    args = ap.parse_args()

    sh = abrir_planilha()
    ensure_tabs(sh)

    prompts = [r for r in sh.worksheet("prompts").get_all_records()
               if r.get("prompt") and ativo(r.get("ativo", "sim"))]
    if args.limite:
        prompts = prompts[:args.limite]
    tabela = {r["dominio"].strip().lower(): r["categoria"]
              for r in sh.worksheet("dominios").get_all_records() if r.get("dominio")}

    jobs = [(p, m) for p in prompts for m in MOTORES]
    with ThreadPoolExecutor(max_workers=4) as pool:
        resultados = list(pool.map(lambda j: rodar_job(*j), jobs))

    # classifica os domínios que ainda não estão na tabela (e guarda para as próximas execuções)
    desconhecidos = {}
    for r in resultados:
        for link in r["links"]:
            d = dominio_de(link["url"])
            if d and buscar_categoria(d, tabela) is None:
                desconhecidos.setdefault(d, link.get("title", ""))
    if desconhecidos:
        try:
            novos = classificar_dominios(desconhecidos)
        except Exception:
            novos = {d: "Outros" for d in desconhecidos}
        tabela.update(novos)
        sh.worksheet("dominios").append_rows(
            [[d, c, "modelo"] for d, c in novos.items()], insert_data_option="INSERT_ROWS")

    linhas_exec, linhas_forn, linhas_fontes = [], [], []
    for r in resultados:
        linhas_exec.append([
            r["execucao_id"], r["data_hora"], r["prompt_id"], r["motor"], r["modelo"],
            r["status"], len(r["fornecedores"]), len(r["links"]), r["texto"][:LIMITE_CELULA],
        ])
        for pos, nome in enumerate(r["fornecedores"], 1):
            linhas_forn.append([r["execucao_id"], r["data_hora"], r["prompt_id"], r["motor"], pos, nome])
        for link in r["links"]:
            d = dominio_de(link["url"])
            linhas_fontes.append([
                r["execucao_id"], r["data_hora"], r["prompt_id"], r["motor"],
                link["ordem_pesquisa"], link["ordem_citacao"], link["url"], d,
                link.get("title", ""), buscar_categoria(d, tabela) or "Outros",
            ])

    for aba, linhas in (("execucoes", linhas_exec), ("fornecedores", linhas_forn), ("fontes", linhas_fontes)):
        if linhas:
            sh.worksheet(aba).append_rows(linhas, value_input_option="RAW", insert_data_option="INSERT_ROWS")

    ok = sum(1 for r in resultados if r["status"] == "ok")
    print(f"{ok}/{len(resultados)} consultas ok | {len(linhas_forn)} fornecedores | {len(linhas_fontes)} links")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
