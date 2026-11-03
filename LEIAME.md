# Monitor de visibilidade em IA (ChatGPT + Gemini → Google Sheets)

A cada 6h, cada prompt ativo é enviado ao ChatGPT e ao Gemini (ambos com busca na web). O script grava:

- **execucoes**: resposta completa, modelo e status de cada consulta
- **fornecedores**: fornecedores citados, na ordem em que aparecem (posição 1, 2, 3...)
- **fontes**: links pesquisados e citados, com a ordem de cada um, o domínio e a categoria
- **dominios**: tabela domínio → categoria (cresce sozinha: domínios novos são classificados por IA e salvos)
- **prompts**: sua lista de 10 prompts, dos quais os 5 primeiros começam ativos. Edite à vontade; a coluna `ativo` aceita sim/não

Categorias de link: Site oficial do fornecedor · Blog de fornecedor/SaaS · Hub de comparação e reviews · Grande portal · Imprensa especializada · Post compartilhado/rede social · Portal menor/hub de conteúdo · Outros.
Para corrigir uma classificação, mude a categoria na aba `dominios`; vale para as próximas execuções.

## Configuração (uns 20 minutos)

1. **Planilha**: crie uma planilha vazia no Google Sheets e copie o ID da URL (o trecho entre `/d/` e `/edit`).
2. **Conta de serviço do Google**: no Google Cloud Console, crie um projeto, ative a *Google Sheets API*, crie uma *conta de serviço* e gere uma chave JSON. Compartilhe a planilha com o e-mail da conta de serviço (perfil *Editor*).
3. **Chaves de API**: crie uma chave da OpenAI e uma do Google AI Studio (Gemini).
4. **Repositório no GitHub**: suba estes arquivos. O `monitor.yml` precisa ficar em `.github/workflows/monitor.yml`. O zip já traz nessa pasta.
5. **Secrets** (Settings → Secrets and variables → Actions): `OPENAI_API_KEY`, `GEMINI_API_KEY`, `SHEET_ID` e `GOOGLE_SERVICE_ACCOUNT_JSON` (o conteúdo inteiro do arquivo JSON).
6. **Teste**: aba Actions → monitor-ia → *Run workflow*, com `limite` = 2. As abas são criadas sozinhas na primeira execução.
7. Conferiu a planilha? Rode de novo sem limite. Depois o agendamento assume.

Para testar no seu computador: `pip install -r requirements.txt`, defina as 4 variáveis de ambiente e rode `python monitor.py --limite 2`.

## Pontos de atenção

- **Nomes de modelos**: os padrões estão em `OPENAI_MODEL` (gpt-5), `GEMINI_MODEL` (gemini-2.5-flash) e `EXTRACT_MODEL` (gpt-4o-mini, usado para extrair fornecedores e classificar domínios). Se algum não existir na sua conta, defina a variável com um modelo disponível.
- **API não é o aplicativo**: pela API, ChatGPT e Gemini respondem sem memória, histórico ou personalização do usuário, então o resultado pode diferir do que as pessoas veem nos apps. Serve para acompanhar tendência.
- **Gemini**: a API devolve só as fontes usadas na resposta (não a lista completa de buscas). Os links vêm como redirecionamento do Google, e o script segue o redirecionamento até o site real.
- **Variação**: a mesma pergunta pode dar respostas diferentes. Compare médias por dia ou semana, não uma execução isolada.
- **GitHub Actions**: o agendamento pode atrasar alguns minutos, e o GitHub pausa agendamentos de repositórios públicos sem atividade por 60 dias (basta reativar). Em repositório privado no plano gratuito há limite de minutos por mês; cada execução leva poucos minutos, mas confira o consumo na primeira semana.
- **Custo**: 5 prompts ativos × 2 motores × 4 execuções = 40 consultas por dia, mais as chamadas de extração. Os prompts P06 a P10 estão com `ativo` = não; para ligá-los, mude para sim na aba `prompts` da planilha. Meça com `limite` = 2 antes de ligar tudo. Para mudar a frequência, edite o cron em `.github/workflows/monitor.yml` (ex.: `0 */12 * * *` para 2 vezes por dia, ou `0 */2 * * *` para voltar a cada 2h).
- **Tamanho da planilha**: a aba `fontes` ganha alguns milhares de linhas por dia. O Google Sheets aguenta alguns meses; depois disso, considere BigQuery ou Supabase.

## Análises sugeridas (tabelas dinâmicas na própria planilha)

- Fornecedores: linhas = `fornecedor`, colunas = `motor`, valores = contagem. Para a posição média, use `posicao` com média.
- Categorias de link: linhas = `categoria`, colunas = `motor`, valores = contagem de `url`. Filtre `ordem_citacao` não vazia para ver só os links citados.
- Por prompt: coloque `prompt_id` nas linhas ou use como filtro.
