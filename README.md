# 🏗️ ObraTrack AI

**Diário de obra com IA multimodal para pequenas reformas e construções.**
O encarregado grava um áudio de 30 segundos e tira uma foto no canteiro. O ObraTrack AI devolve um relatório técnico do dia, uma justificativa formal de atrasos e o registro **As-Built** (por onde passam canos, conduítes e fiação), tudo vinculado ao bloco daquela cliente.

> Projeto de portfólio de **Produto de IA aplicada à Construção Civil**: Python, Streamlit, SQLite e Google Gemini 3.8 Flash (áudio + imagem + saída estruturada).

---

## 1. A dor de negócio

Pequenos empreiteiros e prestadores de reforma (o mercado das "obras da Dona Maria") perdem dinheiro e confiança em três pontos:

| Dor | O que acontece hoje | Custo real |
|---|---|---|
| **Rastreabilidade da reforma** | O que foi feito em cada dia fica na cabeça do pedreiro ou espalhado em mensagens de WhatsApp. | A cliente não percebe o avanço, cobra prazo e questiona a medição e o pagamento. |
| **Justificativa formal de atrasos** | Chuva, falta de funcionário ou falta de material não ficam registradas no dia em que acontecem. | Sem prova datada, o atraso vira culpa do prestador: desconto, multa ou fim do contrato. |
| **Mapeamento antes do reboco** | Depois que a parede é rebocada e revestida, ninguém sabe exatamente por onde passa o cano de água quente ou o conduíte. | Furos em tubulação, quebradeira em manutenção futura e retrabalho caro. |

O registro de campo **precisa custar menos de um minuto**, senão ninguém faz. Por isso a entrada é voz e foto, não formulário.

## 2. A solução

1. **Bloco de Cliente/Obra**: cada obra é um "bloco" com cliente, endereço, escopo e status. Os blocos ficam na barra lateral, com busca rápida por cliente ou endereço e o botão **➕ Criar Novo Bloco de Cliente**. Sem bloco aberto, a tela inicial mostra o cadastro em destaque e atalhos para as obras; o bloco recém-criado abre automaticamente.
   - **Busca Global na Obra**: procura em ambiente, tarefa, transcrição, resumo e detalhes do dia, atrasos e serviço/descrição das fotos, sem diferenciar maiúsculas nem acentos. Os **filtros avançados** combinam ambiente, status da tarefa, intervalo de datas e "apenas dias com atraso", e valem para o checklist, o histórico e a galeria. O botão **✖️ Limpar Filtros**, ao lado da busca, zera tudo de uma vez.
   - **🔎 Resultados Encontrados (Dossiê Completo)**: ao digitar uma busca (ex.: "vala de infiltração") ou escolher um filtro, aparece logo abaixo da barra um card por tarefa encontrada, com tudo no mesmo lugar: status (✅ Concluída / ⏳ Pendente), ambiente e **📅 Realizado em: DD/MM/AAAA**; o resumo, os detalhes de execução/imprevistos, a justificativa de atraso, a transcrição e o áudio do dia; e as fotos do serviço com legenda As-Built e opção de ampliar. Os dados vêm de `database.buscar_dossie_tarefas`, que faz `LEFT JOIN` da tarefa com o registro diário (de origem ou do mesmo dia) e com as fotos (da tarefa, ou soltas do mesmo ambiente e registro/dia).
2. **Novo Registro**: grava o áudio pelo navegador do celular, anexa **uma ou várias fotos** e uma observação opcional. Cada foto vai para a Galeria já no ambiente e no serviço do relato ("foi retirado a cerâmica do banheiro" + 2 fotos → 2 fotos em **Banheiro**, serviço "Retirada da cerâmica...").
3. **IA multimodal (Gemini 3.x Flash)**: cruza o relato, as fotos e o **contexto da obra** e devolve um JSON validado:
   - `transcricao_audio`: transcrição literal e completa do relato;
   - `tarefas_extraidas`: cada atividade falada vira um bloco **por cômodo** ("tirei a cerâmica do banheiro social e passei cano na cozinha" → 2 tarefas), marcada como feita ou pendente;
   - `resumo_executivo`: atividades do dia em linguagem técnica para a cliente;
   - `detalhes_execucao_imprevistos`: particularidades técnicas do serviço, dificuldades do terreno/estrutura, clima ou justificativas de prazo. Não há nota de produtividade: serviços complexos levam mais tempo sem que isso seja baixa produtividade;
   - `motivo_atraso`: chuva, faltas ou problemas técnicos, redigidos como justificativa de prazo;
   - `mapeamento_asbuilt`: descrição técnica das fotos (posição e trajeto de tubulações, alturas, referências);
   - `fotos_classificadas`: ambiente, título do serviço e descrição técnica de **cada foto**.

   **Ambiente sempre pelo cômodo citado**: palavras como banheiro, suíte, cozinha, sala, quarto definem o ambiente; serviços internos (cerâmica, piso, azulejo, cano, hidráulica, fiação) nunca ficam em "Área Externa". A regra está no prompt e é aplicada de novo sobre a resposta da IA e na análise offline (`database.corrigir_ambiente`). "Área Externa" fica para quintal, fossa, vala, muro e calçada.
4. **Checklist por Ambiente**: as tarefas extraídas alimentam um checklist interativo agrupado por cômodo (🚿 Banheiro, 🍳 Cozinha...). Em cada cômodo, **⏳ Pendente / A Fazer** e **✅ Feito / Concluído** ficam separados; um toque no checkbox já move a tarefa de seção. Cada tarefa tem ✏️ editar (texto e ambiente; as fotos acompanham a troca), 📷 anexar fotos e 🗑️ excluir. O formulário **➕ Adicionar tarefa** também aceita fotos. Se um filtro esconder tarefas, aparece um aviso com o botão **Mostrar todas as X tarefas da obra**. O botão **👁️ Ver detalhes do dia e fotos** de cada tarefa abre ali mesmo o dossiê dela (data de execução, relato do dia, áudio e fotos). Ao marcar uma tarefa como feita, o dia fica gravado (`data_conclusao`) e vira o "Realizado em".
   Contadores por cômodo ("Banheiro Social: 3/4 concluídas"). **Recomendação inteligente de ambientes**: os cômodos mais usados nesta obra e nas outras (vindos dos áudios ou digitados) aparecem no topo, seguidos dos ambientes base. Também é possível digitar um ambiente novo.
5. **Histórico e Diários**: linha do tempo por obra com player do áudio original, detalhes de execução/imprevistos, alertas de atraso e as fotos do dia. Cada registro pode ser editado (para corrigir a transcrição, por exemplo) ou excluído com confirmação (tarefas e fotos podem ser mantidas na obra).
6. **🖼️ Galeria da Obra & Relatórios**:
   - **Galeria por ambiente e serviço**: as fotos ficam na tabela `midias_obra`, ligadas a ambiente, serviço e, quando houver, ao registro e à tarefa. Cada cômodo é um bloco com título em destaque (`🚿 Banheiro`); dentro dele, cada serviço tem um subtítulo (*Retirada da cerâmica do banheiro — 11/11/2026*) e a grade das fotos, com 🔍 ampliar, ✏️ editar ambiente/serviço/descrição e 🗑️ excluir. Há botões para adicionar fotos em qualquer ambiente, e filtros por ambiente e palavra-chave. Funciona como manual de manutenção da obra.
   - **📦 ZIP para a arquiteta**: as fotos vêm em subpastas por ambiente (`Banheiro_Social/01_2026-11-11_retirada_da_ceramica.jpg`, `Cozinha/...`), mais o `Memorial_Descritivo_AsBuilt.txt` com a descrição de cada foto, o histórico e o checklist. O ZIP é gerado em memória, na hora do clique.
   - **Migração automática**: fotos de registros antigos entram em `midias_obra` ao iniciar o app, no ambiente das tarefas daquele registro (ou do cômodo citado no texto/escopo, em vez de "Área Externa" genérica).
   - **Resumo para WhatsApp**: texto pronto para copiar, com barra de progresso, serviços concluídos por cômodo e justificativas formais de atraso.
   - **Relatório completo**: HTML autocontido e estilizado para impressão (checklist, justificativas, galeria e diário), pronto para salvar em PDF.

### Indicadores do bloco
Só dados objetivos, sem nota subjetiva do dia:
- **Progresso do Checklist (%)**: percentual de tarefas concluídas.
- **Tarefas Concluídas / Total**: ex.: 7/12.
- **Dias Registrados**: dias distintos com registro no diário.
- **Fotos Mapeadas (As-Built)**: fotos técnicas na Galeria da Obra.

## 3. Arquitetura

```
 Celular / navegador (Streamlit, layout mobile-first)
   │  st.audio_input (voz) · st.file_uploader (foto) · texto
   ▼
 app.py ── security.py: valida magic bytes, remove EXIF/GPS, salva em uploads\<uuid4>.ext
   │
   ├──► ai_service.py ── google-genai ──► cadeia de modelos Gemini
   │        • 3.8 → 3.7 → 3.6 → 3.5 Flash → 2.x (503/429: retry em 2 s; 404/504: próximo modelo)
   │        • áudio ≤15 MB inline (Part.from_bytes); maior → Files API
   │        • foto via PIL (corrige rotação EXIF)
   │        • prompt com o contexto da obra (escopo, endereço, status)
   │        • response_schema = AnaliseDiarioObra + list[ItemTarefa] (Pydantic), temperature 0.2
   │        • todos falharam → análise offline por regras (o registro nunca se perde)
   │        ◄── JSON validado
   │
   └──► database.py ── SQLite (obratrack.db)
            clientes_obras (1) ───< registros_diarios (N)
                   │                       │ (SET NULL ao excluir o registro)
                   └──────< tarefas_obra (N) >┘
```

| Camada | Arquivo | Responsabilidade |
|---|---|---|
| Interface | `app.py` | Blocos de obra, gravação de áudio, checklist, histórico com edição e exclusão, relatório HTML |
| IA | `ai_service.py` | Montagem multimodal, prompt de domínio, saída estruturada (Pydantic), cadeia de modelos, análise offline |
| Dados | `database.py` | Schema SQLite com FK, CHECK constraints e WAL, migrações, CRUD completo, limpeza de mídias órfãs |
| Segurança | `security.py` | Remoção de EXIF/GPS, validação de mídia por magic bytes, nomes UUID, higienização de textos, PIN de acesso |

**Decisões de produto e engenharia**
- **Saída estruturada com Pydantic**: o modelo é obrigado a responder no schema (`AnaliseDiarioObra`). Nada de interpretar texto livre.
- **Contexto da obra no prompt**: a IA interpreta o relato em relação ao escopo contratado (ambientes e serviços), não no vazio.
- **Temperatura 0.2**: o registro tem valor quase jurídico; consistência vale mais que criatividade.
- **`pathlib.Path` em todo o projeto**: caminhos relativos no banco e compatibilidade total com Windows.
- **Chave de API somente no backend**: a `GEMINI_API_KEY` é lida apenas do ambiente do servidor (variável de ambiente ou `.env`). A interface não recebe nem exibe a chave.
- **Banco e mídias confinados ao diretório local**: o `obratrack.db` e a pasta `uploads\` são validados para ficarem dentro do projeto, fora de unidades de rede, e só são aceitas mídias de dentro de `uploads\`.
- **Resiliência a 503**: o retry interno do SDK é desligado e a aplicação percorre uma cadeia de modelos. Se todos falharem, o registro é salvo com uma análise por regras locais e o usuário pode editá-lo depois. Detalhes técnicos vão só para o log do servidor; a interface nunca menciona chave, modelos ou configuração.
- **Privacidade e compressão das fotos**: toda foto é regravada a partir apenas dos pixels. Saem GPS (endereço da cliente), data/hora original, modelo do celular, XMP e ICC; a rotação é aplicada antes. A foto é reduzida para no máximo 1600×1600 px e salva em JPEG (qualidade 78, `optimize`), o que corta 90% ou mais do tamanho. As miniaturas do relatório e as fotos do ZIP passam pelo mesmo processo.
- **Upload validado pelo conteúdo**: áudio só WAV, MP3 ou OGG e foto só JPEG ou PNG, reconhecidos pelos magic bytes e não pela extensão. Limite de 15 MB, também no servidor (`server.maxUploadSize`). O arquivo é salvo como `uploads\<uuid4>.<ext>`, sem nome original, nome do cliente ou data.
- **Textos higienizados**: entradas com `strip`, sem caracteres de controle e com limite de tamanho. Todo texto inserido em HTML customizado passa por `html.escape`, contra XSS nos cards e no relatório.
- **PIN de acesso opcional** (`APP_PIN`): sem o PIN nenhum dado de cliente é renderizado. Após 5 erros, a origem fica bloqueada por 5 minutos, mesmo recarregando a página. A comparação usa `hmac.compare_digest`.
- **SQLite com `foreign_keys = ON` e `journal_mode = WAL`** em todas as conexões, para integridade transacional.
- **Interface sem menus técnicos e com modo escuro**: `.streamlit/config.toml` com `toolbarMode = "viewer"` (sem Rerun, Clear cache e Deploy, mas com o menu ⋮ > Settings para trocar o tema), `showErrorDetails = "none"` (sem traceback na tela) e `gatherUsageStats = false`. O tema padrão é o escuro, com paletas executivas para os temas escuro e claro. O CSS esconde só o rodapé e o botão Deploy, e usa cores translúcidas que funcionam nos dois temas.
- **Configuração do servidor**: `GEMINI_API_KEY` (obrigatória para IA), `APP_PIN` (opcional), `OBRATRACK_MODELOS` (ordem da cadeia, separada por vírgula) e `OBRATRACK_MODO_OFFLINE=1` (força a análise offline em testes).

## 4. Como rodar (Windows PowerShell)

Pré-requisitos: Python 3.11+ e uma chave do Gemini ([Google AI Studio](https://aistudio.google.com/apikey)).

```powershell
git clone <url-do-repositorio> obratrack-ai
cd obratrack-ai

# Ambiente virtual e dependências
python -m venv venv
.\venv\Scripts\pip.exe install -r requirements.txt

# Chave da API
Copy-Item .env.example .env
notepad .env        # GEMINI_API_KEY=AIza...

# (Opcional) cria o banco com as 2 obras de exemplo
.\venv\Scripts\python.exe database.py

# Sobe a aplicação
.\venv\Scripts\python.exe -m streamlit run app.py
```

Abra `http://localhost:8501`. O banco e as obras de exemplo ("Reforma Banheiro - Dona Maria" e "Muro e Calçada - Sr. João") são criados automaticamente na primeira execução.

**Testar pelo celular na mesma rede Wi-Fi**: use o endereço *Network URL* que o Streamlit mostra no terminal (ex.: `http://192.168.0.10:8501`).
> O navegador só libera o microfone em **HTTPS ou localhost**. Para gravar áudio pelo celular, publique no Streamlit Community Cloud ou use um túnel HTTPS (ex.: `cloudflared tunnel --url http://localhost:8501`). Pela rede local, foto e texto funcionam normalmente.

## 5. Estrutura

```
obratrack-ai\
├── app.py            # Interface Streamlit
├── ai_service.py     # Integração Gemini (multimodal + schema)
├── database.py       # SQLite: schema, CRUD e seed
├── security.py       # EXIF/GPS, validação de mídia, UUID, sanitização, PIN
├── .streamlit\
│   └── config.toml   # tema escuro/claro, menus técnicos ocultos, limite de upload
├── requirements.txt
├── .env.example
├── obratrack.db      # gerado (ignorado no git)
└── uploads\          # áudios e fotos por obra (ignorado no git)
```

## 6. Roadmap

- [ ] Exportação nativa em PDF com assinatura da cliente
- [ ] Captura de geolocalização e data/hora da foto (EXIF) como prova adicional
- [ ] Integração com a previsão do tempo para validar justificativas climáticas
- [ ] Multiusuário (encarregado × engenheiro × cliente) com autenticação
- [ ] Planta baixa com marcação visual das tubulações mapeadas
- [ ] Migração do SQLite para PostgreSQL e da mídia para armazenamento em nuvem
