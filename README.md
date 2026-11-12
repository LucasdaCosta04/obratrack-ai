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

1. **Bloco de Cliente/Obra**: cada obra é um "bloco" com cliente, endereço, escopo e status.
2. **Registrar Dia**: grava o áudio pelo navegador do celular, anexa a foto e escreve uma observação opcional.
3. **IA multimodal (Gemini 3.8 Flash)**: cruza o relato, a foto e o **contexto da obra** e devolve um JSON validado:
   - `transcricao_audio`: transcrição fiel do relato;
   - `resumo_executivo`: atividades do dia em linguagem técnica para a cliente;
   - `nivel_produtividade`: Alta, Média ou Baixa (entregas versus imprevistos);
   - `motivo_atraso`: chuva, faltas ou problemas técnicos, redigidos como justificativa de prazo;
   - `mapeamento_asbuilt`: descrição técnica da foto (posição e trajeto de tubulações, alturas, referências).
4. **Histórico e As-Built**: linha do tempo por obra, com badge de produtividade, alertas de atraso e foto ao lado do mapeamento.
5. **Relatório para o Cliente**: consolida as justificativas de atraso e as fotos em um HTML autocontido, pronto para enviar ou imprimir em PDF.

### Indicadores do bloco
- **Total de Registros**: disciplina de documentação da equipe.
- **Dias com Atraso Justificado**: base para pedir aditivo de prazo.
- **Produtividade Predominante**: saúde da obra num só número.

## 3. Arquitetura

```
 Celular / navegador (Streamlit, layout mobile-first)
   │  st.audio_input (voz) · st.file_uploader (foto) · texto
   ▼
 app.py ──────────── salva mídia em uploads\obra_<id>\<tipo>_<timestamp>_<uuid>.ext
   │
   ├──► ai_service.py ── google-genai ──► Gemini 3.8 Flash
   │        • áudio ≤15 MB inline (Part.from_bytes); maior → Files API
   │        • foto via PIL (corrige rotação EXIF)
   │        • prompt com o contexto da obra (escopo, endereço, status)
   │        • response_schema = AnaliseDiarioObra (Pydantic), temperature 0.2
   │        ◄── JSON validado
   │
   └──► database.py ── SQLite (obratrack.db)
            clientes_obras (1) ───< registros_diarios (N)
```

| Camada | Arquivo | Responsabilidade |
|---|---|---|
| Interface | `app.py` | Blocos de obra, gravação de áudio, histórico, relatório HTML |
| IA | `ai_service.py` | Montagem multimodal, prompt de domínio, saída estruturada (Pydantic) |
| Dados | `database.py` | Schema SQLite com FK e CHECK constraints, CRUD, seed de exemplo |

**Decisões de produto e engenharia**
- **Saída estruturada com Pydantic**: o modelo é obrigado a responder no schema, e `nivel_produtividade` é um `Literal` com os mesmos valores aceitos pelo banco (`CHECK`). Nada de interpretar texto livre.
- **Contexto da obra no prompt**: a IA avalia a produtividade em relação ao escopo contratado, não no vazio.
- **Temperatura 0.2**: o registro tem valor quase jurídico; consistência vale mais que criatividade.
- **`pathlib.Path` em todo o projeto**: caminhos relativos no banco e compatibilidade total com Windows.
- **Chave de API somente no backend**: a `GEMINI_API_KEY` é lida apenas do ambiente do servidor (variável de ambiente ou `.env`). A interface não recebe nem exibe a chave.
- **Banco e mídias confinados ao diretório local**: o `obratrack.db` e a pasta `uploads\` são validados para ficarem dentro do projeto, fora de unidades de rede, e só são aceitas mídias de dentro de `uploads\`.
- **Modo Demonstração**: sem chave válida, o registro é salvo com uma análise por regras locais, sem travar o fluxo.

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
