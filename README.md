# 🏗️ ObraTrack AI

**Diário de obra com IA multimodal para pequenas reformas e construções.**
O encarregado grava um áudio de 30 segundos e tira uma foto no canteiro. O ObraTrack AI devolve um relatório técnico do dia, uma justificativa formal de atrasos e o registro **As-Built** (por onde passam canos, conduítes e fiação), tudo vinculado ao bloco daquela cliente.

> Projeto de portfólio de **Produto de IA aplicada à Construção Civil**: Python, Streamlit, SQLite e Google Gemini 3.8 Flash (áudio + imagem + saída estruturada).

---

## 📌 Status do Projeto

![versão](https://img.shields.io/badge/vers%C3%A3o-v1.0.0-1d4ed8) ![status](https://img.shields.io/badge/status-MVP%20validado-16a34a) ![uso](https://img.shields.io/badge/uso-demonstra%C3%A7%C3%A3o%20%2F%20PoC-64748b)

**v1.0.0: MVP validado.** Esta versão encerra a fase de prova de conceito. O ciclo completo funciona de ponta a ponta e foi testado com dados reais de obra:

| Capacidade | Situação na v1.0.0 |
|---|---|
| Registro por voz + fotos + texto, com transcrição pt-BR garantida | ✅ Funcional |
| IA multimodal: tarefas por cômodo e **vínculo visual foto ↔ tarefa** (`foto_indices`) | ✅ Funcional, com fallback offline |
| Checklist por ambiente, dossiê por tarefa, busca global e filtros | ✅ Funcional |
| Privacidade das fotos (EXIF/GPS removidos, nomes UUID) e PIN de acesso | ✅ Funcional |
| Exportação ZIP As-Built organizada por obra/cômodo, **deduplicada por SHA-256** | ✅ Funcional |
| Relatório HTML/PDF e resumo para WhatsApp | ✅ Funcional |

**Escopo e limites:** a v1.0.0 serve para **demonstração e prova de conceito**. Ela roda como instância única (uma equipe, um servidor, banco SQLite local) e não deve ser tratada como ambiente de produção multi-cliente. A evolução para produção está descrita em [Evolução Arquitetural (Roadmap V2)](#7-evolução-arquitetural-roadmap-v2). A V2 será desenvolvida em um **repositório dedicado**, e este repositório fica congelado como referência do MVP.

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
2. **Novo Registro**: grava o áudio pelo navegador do celular, anexa **uma ou várias fotos** e uma observação opcional.
   - **Duas formas de anexar fotos**, em todos os lugares (Novo Registro, ➕ Adicionar tarefa, 📷 de cada tarefa e Galeria): **📸 Abrir Câmera do Celular (Alta Qualidade)** e **🖼️ Escolher da Galeria do Aparelho** (seleção múltipla). O botão de câmera é um `st.file_uploader` que recebe, via script, `accept="image/*"` e `capture="environment"`: no Android/iOS o toque abre direto o **app nativo de câmera traseira**, na resolução máxima (o `st.camera_input`, que usa o vídeo do navegador em baixa resolução, não é mais usado). Cada foto confirmada entra numa lista da sessão (`st.session_state["fotos_capturadas_buffer"]`, sem duplicar, até 10), com miniatura, tamanho já tratado em KB e 🗑️ para descartar; o botão vira **📸 Bater outra foto**. As fotos da lista são salvas junto com o formulário. No computador, o mesmo botão abre o seletor de arquivos.
   - **Tratamento de toda foto** (`security.strip_image_exif`, chamado por `validate_and_save_media`): `ImageOps.exif_transpose` (foto em pé continua em pé), remoção de 100% do EXIF/GPS, redução com `Image.Resampling.LANCZOS` para no máximo 2048 px e JPEG qualidade 85 (`optimize=True`), cerca de 200–350 KB por foto, com nome UUIDv4. Uma foto já tratada (JPEG só com JFIF, sem metadados) não é recomprimida.
3. **Transcrição garantida do áudio (pt-BR)**: antes de qualquer análise, `ai_service.transcrever_audio_garantido` converte a fala em texto com a biblioteca `SpeechRecognition` (reconhecimento de voz gratuito do Google). Qualquer áudio é convertido para WAV PCM 16 kHz mono (`pydub` + ffmpeg do `imageio-ffmpeg`) e dividido em trechos de até 35 s, cortados num silêncio para não partir palavras. Assim, relatos de mais de 1 minuto são transcritos por inteiro. Esse texto literal vai para `transcricao_audio`, aparece aberto logo abaixo do player (🎙️ *O que foi falado no áudio: "..."*) e é a base do resumo, dos detalhes e das tarefas, com ou sem IA. Sem IA disponível, as regras locais quebram a fala (sem pontuação) em tarefas por cômodo. Registros antigos sem transcrição ganham o botão **🔄 Transcrever o áudio agora**, que transcreve o áudio salvo, refaz o resumo do dia e cria/atualiza as tarefas por ambiente (mantendo o status marcado no checklist) e liga as fotos soltas ao serviço certo. O áudio é enviado ao serviço de voz do Google, por isso é preciso internet.
   **IA multimodal (Gemini 3.x Flash)**: recebe a transcrição literal junto com o áudio e as fotos, cruza tudo com o **contexto da obra** e devolve um JSON validado:
   - `transcricao_audio`: transcrição literal e completa do relato;
   - `tarefas_extraidas`: cada serviço falado vira um bloco **por cômodo**, com `resumo_servico`, `detalhes_servico` **exclusivos** daquele serviço e `foto_indices`, a lista das imagens que mostram visualmente aquela tarefa (ver [vínculo multimodal](#vínculo-multimodal-foto--tarefa)) (um relato sobre banheiro e vala gera uma tarefa do Banheiro e outra da Área Externa, sem misturar os textos). O analisador local faz a mesma separação: remove saudações/despedidas, quebra a fala sem pontuação em trechos e agrupa os trechos por ambiente ("tirei a cerâmica do banheiro social e passei cano na cozinha" → 2 tarefas), marcada como feita ou pendente;
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
   - **📦 ZIP para a arquiteta**: pasta da obra com as fotos em subpastas por cômodo, sem arquivos repetidos, mais o relatório e o memorial descritivo na raiz. O ZIP é gerado em memória, na hora do clique. Estrutura e regras em [Estrutura de Exportação](#4-estrutura-de-exportação-zip-as-built).
   - **Migração automática**: fotos de registros antigos entram em `midias_obra` ao iniciar o app, no ambiente das tarefas daquele registro (ou do cômodo citado no texto/escopo, em vez de "Área Externa" genérica).
   - **Resumo para WhatsApp**: texto pronto para copiar, com barra de progresso, serviços concluídos por cômodo e justificativas formais de atraso.
   - **Relatório completo**: HTML autocontido e estilizado para impressão (checklist, justificativas, galeria e diário), pronto para salvar em PDF.

### Indicadores do bloco
Só dados objetivos, sem nota subjetiva do dia:
- **Progresso do Checklist (%)**: percentual de tarefas concluídas.
- **Tarefas Concluídas / Total**: ex.: 7/12.
- **Dias Registrados**: dias distintos com registro no diário.
- **Fotos Mapeadas (As-Built)**: fotos técnicas na Galeria da Obra (arquivos únicos: uma foto ligada a duas tarefas conta uma vez).

### Estabilidade no navegador
O tradutor automático do Chrome alterava textos ("anéis" → "animais") e nós do DOM controlados pelo React, derrubando a tela com `NotFoundError: removeChild`. Um script num iframe invisível marca a página como `lang="pt-BR"`, `translate="no"` e `class="notranslate"`, com a meta `google=notranslate`. Todos os widgets têm `key` única e estável.

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
                   ├──────< tarefas_obra (N) >┘
                   └──────< midias_obra (N)   fotos ↔ ambiente / registro / tarefa (vinculo_ia)
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
- **Privacidade e compressão das fotos**: toda foto é regravada a partir apenas dos pixels. Saem GPS (endereço da cliente), data/hora original, modelo do celular, XMP e ICC; a rotação é aplicada antes. A foto é reduzida com LANCZOS para no máximo 2048×2048 px e salva em JPEG (qualidade 85, `optimize`), cerca de 200–350 KB, o que preserva a nitidez de fios e conexões e ainda corta 90% ou mais do tamanho. As miniaturas do relatório e as fotos do ZIP passam pelo mesmo processo.
- **Upload validado pelo conteúdo**: áudio só WAV, MP3 ou OGG e foto só JPEG ou PNG, reconhecidos pelos magic bytes e não pela extensão. Limite de 15 MB, também no servidor (`server.maxUploadSize`). O arquivo é salvo como `uploads\<uuid4>.<ext>`, sem nome original, nome do cliente ou data.
- **Textos higienizados**: entradas com `strip`, sem caracteres de controle e com limite de tamanho. Todo texto inserido em HTML customizado passa por `html.escape`, contra XSS nos cards e no relatório.
- **PIN de acesso opcional** (`APP_PIN`): sem o PIN nenhum dado de cliente é renderizado. Após 5 erros, a origem fica bloqueada por 5 minutos, mesmo recarregando a página. A comparação usa `hmac.compare_digest`.
- **SQLite com `foreign_keys = ON` e `journal_mode = WAL`** em todas as conexões, para integridade transacional.
- **Interface sem menus técnicos e com modo escuro**: `.streamlit/config.toml` com `toolbarMode = "viewer"` (sem Rerun, Clear cache e Deploy, mas com o menu ⋮ > Settings para trocar o tema), `showErrorDetails = "none"` (sem traceback na tela) e `gatherUsageStats = false`. O tema padrão é o escuro, com paletas executivas para os temas escuro e claro. O CSS esconde só o rodapé e o botão Deploy, e usa cores translúcidas que funcionam nos dois temas.
- **Configuração do servidor**: `GEMINI_API_KEY` (obrigatória para IA), `APP_PIN` (opcional), `OBRATRACK_MODELOS` (ordem da cadeia, separada por vírgula) e `OBRATRACK_MODO_OFFLINE=1` (força a análise offline em testes).

## 4. Estrutura de Exportação (ZIP As-Built)

O botão **📦 Baixar Fotos da Obra** gera `{slug-da-obra}_{AAAAMMDD}.zip`. Exemplo de uma obra com relatos sobre banheiro e área externa:

```text
reforma-banheiro-dona-maria_20261122.zip
└── reforma-banheiro-dona-maria/                         ← slug da obra (sanitize_slug)
    ├── relatorio_obra.html                              ← relatório completo; as imagens apontam para os .jpg abaixo
    ├── memorial_descritivo_asbuilt.txt                  ← resumo + índice: foto → tarefas, tarefa → fotos
    ├── area-externa/                                    ← slug do cômodo
    │   ├── 2026-11-12_escavacao-da-vala-de-drenagem_01.jpg
    │   └── 2026-11-12_multiplas-tarefas_01.jpg          ← 1 imagem, 2 tarefas (escavação + anéis): gravada UMA vez
    ├── banheiro-social/
    │   ├── 2026-11-11_retirada-da-ceramica_01.jpg
    │   └── 2026-11-11_retirada-da-ceramica_02.jpg       ← sequência por data + tarefa
    └── geral/                                           ← fotos sem cômodo específico
        └── 2026-11-12_registro-fotografico-do-dia_01.jpg

Nome de cada foto: {data}_{slug-da-tarefa}_{sequência}.jpg
```

#### Deduplicação por conteúdo (SHA-256)
- Antes de gravar cada `.jpg`, o gerador calcula `hashlib.sha256(bytes_da_foto).hexdigest()` sobre os bytes exatamente como vão para o ZIP.
- Um registro de `(slug_comodo, sha256)` impede gravar o mesmo conteúdo duas vezes na mesma pasta, mesmo que venha de **arquivos diferentes**. Isso inclui uma foto reenviada ou um registro antigo que duplicou o arquivo para duas tarefas.
- A foto duplicada não é gravada de novo, mas **continua referenciada**. O memorial lista o arquivo em cada tarefa (`foto: reforma-banheiro-dona-maria/area-externa/...`), e o relatório HTML a mostra em cada serviço, sem embutir cópias em base64.
- O mesmo conteúdo em cômodos **diferentes** gera uma cópia por pasta, porque cada pasta precisa ser completa sozinha para quem for fazer a manutenção daquele cômodo.
- Imagem com várias tarefas recebe o nome neutro `multiplas-tarefas`, para não parecer que pertence só à primeira. A sequência conta por data + tarefa e nunca sobrescreve.

#### Vínculo multimodal foto ↔ tarefa
- Cada foto vai ao Gemini precedida do rótulo `Imagem 0:`, `Imagem 1:`… Cada tarefa extraída devolve `foto_indices` com **somente** as imagens que mostram visualmente aquele serviço (`[0, 2]`). Estar no mesmo cômodo não basta.
- Uma imagem que mostra duas tarefas (vala escavada com anéis instalados) pode entrar nas duas. Com 10 fotos de coisas diferentes, cada tarefa recebe só as suas.
- Foto que não corresponde a nenhuma tarefa fica **apenas no registro do cômodo**, marcada com `midias_obra.vinculo_ia = 1`. Assim ela não é puxada depois para tarefas erradas pelas regras automáticas de vínculo por ambiente. Fotos antigas ou manuais (`vinculo_ia` nulo) mantêm o comportamento anterior, sem nenhuma alteração no banco existente.
- Sem IA disponível (modo offline, sem visão), a foto só é ligada se houver uma única tarefa. Com várias tarefas, ela fica solta no registro.

#### Segurança da exportação
- Todo segmento de caminho passa por `security.sanitize_slug`, que remove acentos, espaços e símbolos e impede `../` e nomes reservados do Windows. Depois, `_caminho_zip` recusa qualquer segmento fora do padrão de slug, como segunda barreira contra *zip slip*.
- As fotos saem sem EXIF/GPS. Fotos legadas são regravadas como JPEG limpo antes de entrar no ZIP.
- Os nomes organizados existem só dentro do ZIP. No servidor e no banco, as fotos seguem com nome UUID.

## 5. Como rodar (Windows PowerShell)

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

## 6. Estrutura do código

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

## 7. Evolução Arquitetural (Roadmap V2)

A v1.0.0 cumpriu o objetivo de **validar o produto**: o fluxo voz + foto → diário técnico → checklist → As-Built resolve a dor do canteiro. Os limites que restam não são de funcionalidade, mas de **arquitetura**. Por isso a V2 será desenvolvida em um **repositório dedicado**, e não como refatoração incremental deste código.

### Por que a stack atual não escala para produção

| Limite da v1 (Streamlit + SQLite) | Impacto no canteiro de obras |
|---|---|
| **Streamlit mantém o estado no servidor por WebSocket** e roda o script inteiro a cada interação | Em 3G/4G instável, cada queda de conexão reinicia a sessão. Fotos no buffer e formulários meio preenchidos podem se perder, e cada toque exige ida e volta ao servidor. |
| **Sem modo offline** no navegador | Subsolo, laje e zona rural ficam sem sinal. O registro precisa ser feito na hora, não "quando voltar o 4G". |
| **SQLite em arquivo local**, escrita serializada, um único processo | Não suporta várias equipes gravando ao mesmo tempo nem escalar horizontalmente. O backup depende do disco do servidor. |
| **Isolamento por instância** (um PIN, um banco) | Não há separação de dados entre empresas. Atender vários empreiteiros exigiria uma instalação por cliente. |
| **Mídia em pasta local** servida pela própria aplicação | Não há controle de acesso por arquivo nem CDN, e o disco do servidor é ponto único de falha. |

### Arquitetura alvo

```text
 React PWA (Service Worker + IndexedDB)            FastAPI (async, Pydantic v2)
 ┌──────────────────────────────┐  HTTPS/JSON  ┌──────────────────────────────────┐
 │ captura offline: áudio, fotos│ ───────────► │ auth JWT · fila de análise IA    │
 │ fila de sincronização        │ ◄─────────── │ (Gemini multimodal, foto_indices)│
 │ compressão/EXIF no aparelho  │  URL assinada└───────┬──────────────────┬───────┘
 └──────────────┬───────────────┘                      │                  │
                │ PUT direto (URL assinada, curta)     ▼                  ▼
                ▼                              PostgreSQL + RLS     Storage privado
         Storage privado ◄──────────────────── (tenant_id em todas  (bucket sem acesso
         (S3 / Supabase Storage)                as tabelas)          público)
```

- **React PWA, pensado para redes móveis de canteiro**:
  - um *Service Worker* deixa o app abrir e funcionar sem conexão;
  - áudios, fotos e registros ficam numa **fila local em IndexedDB** e são enviados em segundo plano quando o sinal volta, com novas tentativas (*background sync*). Nenhum registro se perde por queda de rede;
  - a compressão e a remoção de EXIF/GPS rodam **no próprio aparelho**, o que reduz o tráfego de 4–12 MB para cerca de 300 KB por foto antes do envio;
  - a interface responde na hora, sem ida e volta ao servidor a cada toque, e pode ser instalada na tela inicial como app.
- **FastAPI**:
  - API REST *stateless* e assíncrona: uma conexão que cai não destrói a sessão de ninguém, e a API escala horizontalmente;
  - os **schemas Pydantic da v1 (`AnaliseDiarioObra`, `ItemTarefa.foto_indices`) são reaproveitados** como contrato da API e da saída estruturada do Gemini;
  - a análise de IA vira tarefa assíncrona em fila: o upload termina logo e o resultado chega quando estiver pronto, em vez de travar a requisição por até 60 s.
- **PostgreSQL com Row Level Security (multi-tenant)**:
  - todas as tabelas recebem `tenant_id` (empresa), e políticas RLS como `USING (tenant_id = current_setting('app.tenant_id')::uuid)` fazem o **próprio banco** recusar linhas de outra empresa;
  - o isolamento não depende de lembrar um `WHERE` em cada consulta: um bug na API não vaza dados entre empreiteiros;
  - perfis (encarregado, engenheiro, cliente final) viram políticas por papel;
  - de quebra: concorrência real (MVCC), backups *point-in-time* e busca textual em português (`unaccent` + `tsvector`), no lugar do `norm()` + `LIKE` atuais.
- **Storage privado com URLs assinadas**:
  - fotos e áudios ficam num bucket **sem acesso público**;
  - a API, depois de checar o tenant e a permissão, emite **URLs assinadas de curta duração**, tanto para o envio direto do celular ao storage quanto para visualizar e baixar;
  - o arquivo não passa pelo servidor de aplicação, e um link vazado expira em minutos;
  - a deduplicação por SHA-256 da v1 evolui para **armazenamento endereçado por conteúdo**: o hash vira a chave do objeto, e a mesma foto ligada a várias tarefas existe uma única vez no storage.

### O que é preservado da v1
As regras de negócio validadas no MVP migram como estão:
- separação de tarefas por cômodo e correção de ambiente por palavras-chave;
- vínculo visual `foto_indices` e o marcador `vinculo_ia`;
- transcrição pt-BR garantida e fallback offline quando a IA cai;
- sanitização de nomes (`sanitize_slug`) e estrutura do ZIP As-Built deduplicado;
- regra de que **nenhum registro de campo se perde**.

### Backlog de produto (pós-V2)
- [ ] Exportação nativa em PDF com assinatura da cliente
- [ ] Geolocalização e data/hora da foto, coletadas pelo app e guardadas separadas da imagem, como prova adicional sem expor o EXIF
- [ ] Integração com a previsão do tempo para validar justificativas climáticas
- [ ] Planta baixa com marcação visual das tubulações mapeadas
