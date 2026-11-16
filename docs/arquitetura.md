# Arquitetura do J.A.R.V.I.S.

Três visões do mesmo sistema:

1. **Camadas**: do usuário até o sistema operacional.
2. **Processos**: quem é processo de quem, e como eles se comunicam.
3. **Uma ação sensível do começo ao fim**: "encerre o processo calc".

Os diagramas estão em [Mermaid](https://mermaid.js.org/): o GitHub desenha a partir do texto, então dá para
editar e comparar no `git diff` como qualquer código.

## 1. Camadas

```mermaid
flowchart TB
    subgraph USR["Usuário"]
        fala["Voz (microfone)"]
        digita["Texto (chat)"]
    end

    subgraph WEB["Interface: HTML, CSS e JavaScript (src/jarvis/web)"]
        ui["Command Center<br/>núcleo animado, cards e painéis"]
        flut["Janelinha flutuante"]
        ext["Extensão do WhatsApp Web"]
    end

    subgraph SRV["Processo do servidor: Python + FastAPI/Uvicorn"]
        server["server.py<br/>HTTP + WebSocket<br/>envia stats a cada 1,5 s"]
        assistant["assistant.py<br/>histórico, confirmações e fallback offline"]
        brain["brain/<br/>APIs: Claude, GPT, Gemini, Groq, OpenRouter, Ollama<br/>contas: CLIs oficiais · local: regras offline"]
        tools["tools.py<br/>ferramentas que o cérebro pode chamar<br/>confirmação + auditoria"]
        mon["system_monitor.py<br/>CPU, RAM, discos, rede, temperatura, processos"]
        osc["os_control.py<br/>processos, apps, volume, arquivos, energia"]
        integ["spotify · whatsapp · integracoes · web_tools<br/>apps (UI Automation) · trabalho · massivas"]
        voice["voice.py<br/>ElevenLabs ou voz local"]
        cfg["config.py · logger.py"]
    end

    vozsrv["Processo da voz clonada<br/>voz/servidor_voz.py · porta 8001"]

    subgraph SO["Sistema operacional"]
        kernel["Kernel<br/>processos, sinais, /proc e /sys,<br/>APIs do Windows (psutil, ctypes)"]
        fs[("Arquivos<br/>.env · data/ · logs/ · reports/<br/>run/ · backups/")]
    end

    subgraph SH["Scripts: Bash e PowerShell (scripts/)"]
        run["run.sh / run.ps1<br/>start · stop · status"]
        monsh["monitor.sh / monitor.ps1"]
        bkp["backup.sh"]
        cron["cron / Agendador de Tarefas"]
    end

    fala --> ui
    digita --> ui
    ui <-->|"WebSocket /ws"| server
    flut <-->|"WebSocket /ws/flutuante"| server
    ext <-->|"WebSocket /ws/whatsapp"| server
    server --> assistant --> brain
    brain -->|"tool calling"| tools
    server --> mon
    tools --> osc
    tools --> mon
    tools --> integ
    server --> voice
    voice -->|"HTTP local"| vozsrv
    mon --> kernel
    osc --> kernel
    cfg -->|"lê variáveis de ambiente e .env<br/>grava jarvis.log e acoes.jsonl"| fs
    run -->|"inicia em 2º plano, grava o PID<br/>e encerra com SIGTERM"| server
    cron --> monsh
    cron --> bkp
    monsh --> kernel
    monsh -->|"relatório + alertas.log"| fs
    bkp -->|"tar.gz de data/ e logs/"| fs
```

| Camada | O que faz | Arquivos |
|---|---|---|
| Interface | Mostra os dados e recebe comandos. Não acessa o SO: tudo passa pelo servidor | `src/jarvis/web/` |
| Servidor | Um único processo Python. Liga a interface ao cérebro e às ferramentas | `server.py`, `assistant.py` |
| Cérebro | Transforma a frase em **chamadas de ferramenta**. O cérebro *local* funciona sem internet | `brain/` |
| Ferramentas | Lista do que o JARVIS sabe fazer. Pede confirmação nas ações destrutivas e registra tudo em `logs/acoes.jsonl` | `tools.py` |
| Acesso ao SO | Lê os recursos (`psutil`, `/proc`, `/sys`) e age (cria e encerra processos, mexe em arquivos) | `system_monitor.py`, `os_control.py` |
| Scripts | Instalam, iniciam, param, monitoram e fazem backup **sem depender do Python** | `scripts/` |

## 2. Processos

```mermaid
flowchart LR
    shell["Terminal<br/>run.sh / run.ps1"]
    py["python -m jarvis<br/>servidor (porta 8000)"]
    voz["Servidor de voz<br/>outro interpretador Python<br/>porta 8001"]
    apps["Apps abertos pelo JARVIS<br/>calculadora, navegador..."]
    clis["CLIs das contas<br/>Claude Code · Codex · Gemini CLI"]
    srr["StatusRobotReport<br/>programa de console"]
    nav["Navegador dos apps internos<br/>WhatsApp · Instagram · Facebook"]
    db[("SQLite do MHnet Notifier<br/>escrito por outro programa")]
    cron["cron / Agendador"]
    mon["monitor.sh"]
    bkp["backup.sh"]
    pid[("run/jarvis.pid")]
    lock[("run/backup.lock")]

    shell -->|"nohup ... &"| py
    shell -->|"grava $!"| pid
    shell -->|"SIGTERM, depois SIGKILL"| py
    shell -->|"nohup ... &"| voz
    py <-->|"HTTP 127.0.0.1"| voz
    py -->|"processo desacoplado<br/>start_new_session / DETACHED_PROCESS"| apps
    py -->|"subprocess, com tempo limite"| clis
    py -->|"CREATE_NEW_CONSOLE"| srr
    py -->|"janela de navegador com perfil próprio"| nav
    py -.->|"somente leitura (mode=ro)"| db
    cron -->|"a cada 30 min"| mon
    cron -->|"todo dia às 2h"| bkp
    bkp -->|"flock: um backup por vez"| lock
```

- **Arquivo de PID:** o `run.sh` guarda em `run/jarvis.pid` o PID do servidor que acabou de criar (`$!`). O
  `status` e o `stop` usam esse número, e o `kill -0` só pergunta ao SO se o processo ainda existe.
- **Processos desacoplados:** os apps abertos pelo JARVIS ganham uma sessão própria. Se o JARVIS cair, eles
  continuam abertos.
- **Comunicação entre processos:** a voz clonada roda num processo separado, com outro Python e outras
  bibliotecas, e conversa com o servidor por HTTP local. Se esse processo cair, o servidor usa outra voz.
- **Arquivo compartilhado:** o banco SQLite de outro programa é aberto em **somente leitura**, para não
  disputar a escrita com quem é dono dele.
- **No Windows**, o `python.exe` da `.venv` é um lançador que cria o Python de verdade como **processo filho**
  e o coloca num *Job Object*. Por isso o `run.ps1` guarda o PID do lançador, e encerrar o lançador encerra
  os dois (ver [verificacao.md](verificacao.md), passos 1 e 7).

## 3. Uma ação sensível do começo ao fim

```mermaid
sequenceDiagram
    actor U as Usuário
    participant UI as Interface
    participant S as server.py
    participant C as Cérebro
    participant T as tools.py
    participant OS as os_control + SO
    participant L as logs/acoes.jsonl

    U->>UI: "encerre o processo calc"
    UI->>S: {type: "chat", text}
    S->>C: frase + lista de ferramentas
    C->>T: encerrar_processo(alvo="calc")
    T-->>UI: {type: "confirm_request"} com PIDs e memória
    Note over T,OS: nada é executado ainda: o processo continua vivo
    U->>UI: clica em CONFIRMAR (ou diz "confirmar")
    UI->>S: {type: "confirm", id, approve: true}
    S->>T: resolve(id, aprovar)
    T->>OS: terminate() → espera 3 s → kill() nos que sobraram
    OS-->>T: processos encerrados
    T->>L: grava data, ferramenta, argumentos e resultado
    T-->>UI: {type: "action"} e {type: "reply"}
    UI-->>U: "1 processo(s) encerrado(s)."
```

As mensagens trocadas pelo WebSocket estão descritas em [protocolo-websocket.md](protocolo-websocket.md).
