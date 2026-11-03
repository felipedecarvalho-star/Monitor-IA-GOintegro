#!/usr/bin/env python3
"""Cria (se faltar) as abas da planilha e carrega os dados iniciais.

Pode ser rodado sozinho (python setup_sheet.py) ou é chamado automaticamente pelo monitor.py.
Só preenche prompts e domínios quando a aba acabou de ser criada, então é seguro rodar várias vezes.
"""
import csv
import json
import os
from pathlib import Path

import gspread

BASE = Path(__file__).parent

CATEGORIAS = [
    "Site oficial do fornecedor",
    "Blog de fornecedor/SaaS",
    "Hub de comparação e reviews",
    "Grande portal",
    "Imprensa especializada",
    "Post compartilhado/rede social",
    "Portal menor/hub de conteúdo",
    "Outros",
]

HEADERS = {
    "prompts": ["id", "prompt", "ativo"],
    "dominios": ["dominio", "categoria", "origem"],
    "execucoes": [
        "execucao_id", "data_hora", "prompt_id", "motor", "modelo",
        "status", "n_fornecedores", "n_links", "resposta_bruta",
    ],
    "fornecedores": [
        "execucao_id", "data_hora", "prompt_id", "motor", "posicao", "fornecedor",
    ],
    "fontes": [
        "execucao_id", "data_hora", "prompt_id", "motor", "ordem_pesquisa",
        "ordem_citacao", "url", "dominio", "titulo", "categoria",
    ],
}


def read_csv(nome):
    with open(BASE / nome, newline="", encoding="utf-8") as f:
        linhas = list(csv.reader(f))
    return linhas[1:]  # ignora o cabeçalho


def ensure_tabs(sh):
    existentes = {w.title: w for w in sh.worksheets()}
    for titulo, cabecalho in HEADERS.items():
        nova = titulo not in existentes
        if nova:
            ws = sh.add_worksheet(title=titulo, rows=1000, cols=len(cabecalho))
            ws.append_row(cabecalho)
            ws.freeze(rows=1)
            if titulo == "prompts":
                ws.append_rows(read_csv("prompts.csv"), insert_data_option="INSERT_ROWS")
            elif titulo == "dominios":
                linhas = [[d, c, "inicial"] for d, c in read_csv("dominios.csv")]
                ws.append_rows(linhas, insert_data_option="INSERT_ROWS")
    # remove a aba em branco que o Google cria por padrão
    for padrao in ("Sheet1", "Página1", "Planilha1"):
        try:
            ws = sh.worksheet(padrao)
            if not ws.get_all_values() and len(sh.worksheets()) > 1:
                sh.del_worksheet(ws)
        except gspread.WorksheetNotFound:
            pass


def abrir_planilha():
    credenciais = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    gc = gspread.service_account_from_dict(credenciais)
    return gc.open_by_key(os.environ["SHEET_ID"])


if __name__ == "__main__":
    ensure_tabs(abrir_planilha())
    print("Planilha pronta.")
