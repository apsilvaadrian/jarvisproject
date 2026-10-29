# J.A.R.V.I.S. — Assistente Pessoal com Monitoramento e Controle do Sistema Operacional

> *Just A Rather Very Intelligent System* — Projeto Integrador de Sistemas Operacionais

Assistente inspirado no JARVIS do Homem de Ferro: uma interface futurista com um **núcleo central animado**
que monitora em tempo real **CPU, memória RAM, discos, temperatura, rede, GPU e processos**, e que
**controla o computador por voz ou chat**: abre aplicativos, toca músicas no **Spotify**, ajusta o volume,
analisa pastas, gera relatórios e encerra processos (com autorização do usuário).

O "cérebro" da conversa é plugável: **Claude (Anthropic)**, **GPT (OpenAI)**, **Gemini (Google)** ou um
**modo offline** por comandos, que funciona sem internet e sem chave de API.

---

## 1. Problema

Acompanhar o estado do computador exige abrir várias ferramentas diferentes (Gerenciador de Tarefas,
`top`, `df`, `free`, sensores...) e controlar o PC exige cliques ou comandos de terminal que nem todos
conhecem. O JARVIS reúne **monitoramento** e **administração básica** do sistema em um único lugar,
acessível por **linguagem natural** (voz ou texto), com registro de todas as ações executadas.

## 2. Integrantes

| Nome | GitHub | Contribuição |
|---|---|---|
| Adrian Silva | [@apsilvaadrian](https://github.com/apsilvaadrian) | Desenvolvimento da aplicação: núcleo, IA, interface, integrações e controle do SO |
| Igor Correa | [@Igudev2022](https://github.com/Igudev2022) | Validação no Linux, script de backup e documentação dos conceitos de SO |
| Gustavo Marini | [@marini-krod](https://github.com/marini-krod) | Testes, roteiro de verificação, documentação e arquitetura |

As contribuições de cada um aparecem no histórico de commits e no [CHANGELOG](CHANGELOG.md).

## 3. Tecnologias

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn (HTTP + WebSocket) |
| Sistema | `psutil` (lê `/proc` e `/sys` no Linux; APIs do kernel no Windows), `subprocess`, `os`, `ctypes` |
| IA | APIs do Claude, OpenAI e Gemini via `httpx` com *tool calling* |
| Spotify | Spotify Web API via `spotipy` (opcional) |
| Interface | HTML + CSS + JavaScript puro, Canvas 2D (núcleo), Web Speech API (voz) |
| Automação | Bash (Linux) e PowerShell (Windows) |
| Testes | `pytest` |

## 4. Conceitos de Sistemas Operacionais aplicados

| Conceito | Onde aparece | Arquivo |
|---|---|---|
| **Gerenciamento de processos** | Listar processos (PID, estado, usuário, %CPU, memória RSS), buscar por nome, **encerramento controlado** (SIGTERM → espera → SIGKILL), lista de processos protegidos, confirmação obrigatória | `src/jarvis/os_control.py`, `system_monitor.py` |
| **Criação de processos** | Aplicativos são abertos como processos filhos **desacoplados** (`start_new_session` no Linux / `DETACHED_PROCESS` no Windows); o servidor roda em segundo plano com **arquivo de PID** | `os_control.py` (`_spawn`), `scripts/run.sh` |
| **Comunicação entre processos** | A voz clonada roda em outro processo (outro interpretador Python); o JARVIS conversa com ele por HTTP local (`127.0.0.1:8001`), com verificação de saúde e fallback se o processo cair | `voz/servidor_voz.py`, `server.py` (`/api/tts`) |
| **Uso de CPU e memória** | `%CPU` total e por núcleo, *load average*, RAM/swap, calculados pelo kernel; o `monitor.sh` calcula a CPU lendo **`/proc/stat` duas vezes** | `system_monitor.py`, `scripts/monitor.sh` |
| **Armazenamento e sistema de arquivos** | Partições montadas, tipo de FS, espaço livre; análise de diretório (`os.walk`, `stat`) com maiores arquivos e uso por extensão | `system_monitor.py`, `os_control.analyze_directory` |
| **Arquivos e permissões** | Notas em `data/notas.md` (criado com `chmod 600`), `.env` com segredos em modo `600`, diretórios `logs/` `reports/` em `750`; relatórios em `640`; tratamento de `PermissionError` | `scripts/setup.sh`, `os_control.save_note` |
| **Variáveis de ambiente** | Toda a configuração (chaves de API, porta, cérebro) vem do ambiente/`.env`; `run.sh` exporta o `.env` com `set -a` e define `PYTHONPATH` | `config.py`, `scripts/run.sh` |
| **Sinais** | `run.sh stop` envia `SIGTERM` e, se o processo não terminar, `SIGKILL`; `kill -0` para checar se o PID existe | `scripts/run.sh` |
| **Registro operacional (logs)** | `logs/jarvis.log` (rotacionado) e `logs/acoes.jsonl` (auditoria de cada ação, com data, argumentos e resultado) | `logger.py` |
| **Dispositivos / sensores** | Temperatura via `/sys/class/thermal` e `hwmon` (Linux) ou WMI/ACPI (Windows); GPU via `nvidia-smi`; teclas de mídia virtuais via API do Windows | `system_monitor.py`, `os_control.py` |
| **Sessão do usuário e janelas** | Detecta bloqueio/desbloqueio da sessão (`WTSQuerySessionInformation`, Win+L) para dar boas-vindas; esconde/mostra a janela pela API Win32 (`ShowWindow`, `SetWindowPos`) para continuar rodando na bandeja | `desktop.py`, `boas_vindas.py` |
| **Arquivo compartilhado entre processos** | O banco SQLite do MHnet Notifier é aberto em **somente leitura** (`mode=ro`) enquanto o Notifier escreve nele; outro programa (StatusRobotReport) é iniciado num **console próprio** (`CREATE_NEW_CONSOLE`) | `massivas.py`, `trabalho.py` |
| **Agendamento de tarefas** | `monitor.sh` pronto para `cron`; `monitor.ps1` para o Agendador de Tarefas; desligamento agendado via `shutdown` | `scripts/` |

## 5. Funcionalidades

- **Interface "Command Center"** com abas: *Command Center* (núcleo + cards), *AI Core* (escolher e configurar
  os cérebros), *AI Voice* (voz, microfone e palavra de ativação) e *Integrações*. No topo: status geral do
  sistema, sino de alertas, menu de cards e o comandante; embaixo: clima, rede e a barra **Falar com o JARVIS**
  (clique, diga "Jarvis" ou digite).
- **Cards sob controle — seu e do JARVIS**: 18 cards em três colunas (Visão geral com "saúde" do PC,
  Processador, Memória, Agenda, Spotify, Alertas, Conversa, Processos, Ações rápidas, Cérebros, Serviços,
  E-mail, GitHub, Docker...). Arraste pelo título para mudar de lugar, use o pino para **fixar** (fica no topo e
  não some quando o JARVIS mostra cards de contexto) e o ✕ para ocultar; o botão ▦ do topo reabre os ocultos e
  restaura o padrão. Por voz/chat: "fixa o card do Spotify", "move os processos para a esquerda no topo",
  "esconde a rede", "mostra o GitHub", "restaura os cards". O layout fica em `data/cards.json`, compartilhado
  entre a tela e a ferramenta `controlar_cards`.
- **Card em foco**: o botão ⤢ de qualquer card (ou "foca na memória", "traz a agenda pro centro") leva o card,
  ao vivo e maior, para o centro da tela, como a letra da música; o ✕ devolve ao lugar. O JARVIS também faz isso
  sozinho quando a conversa é sobre um card (ex.: "como está a memória?") ou ao mostrar notícias, clima e
  cards da IA: o card fica em foco enquanto ele fala e volta para a coluna quando ele termina.
- **WhatsApp, Instagram e Facebook dentro do JARVIS**: o botão de apps no topo (ou "abre o Instagram aqui") abre
  o painel APPS com abas para os três. Cada um é uma janela de navegador de verdade (perfil próprio,
  `navegador-apps`), sem moldura e encaixada em cima do painel: acompanha a janela, some quando um menu/modal
  abre por cima e minimiza junto. O login é feito uma vez ali (QR code do WhatsApp, conta do Instagram/Facebook)
  e fica salvo. Os apps já usados reabrem escondidos quando o JARVIS inicia, para ele **avisar mensagens e
  notificações novas** (na tela, na janelinha e por voz — dá para desligar a voz nas Configurações). Com o
  WhatsApp logado ali, o JARVIS lê e envia mensagens por ele, sem precisar da extensão (`embutidos.py`).
- **Apps e notícias em cards**: o botão **⧉ CARD** do painel APPS (ou "deixa o WhatsApp num card") põe o app num
  card ao vivo nas colunas — dá para responder o WhatsApp num card e ver reels do Instagram/YouTube em outro ao mesmo
  tempo; o ⧉ do navegador interno faz o mesmo com a notícia (modo leitura). São cards como os outros: arrastam pelo
  título entre as colunas, têm ⤢ foco, 📌 fixar e ✕, e a altura muda puxando a borda de baixo. Os **fixados** também
  aparecem na janelinha flutuante com o JARVIS minimizado (arrastáveis e redimensionáveis, inclusive em outro monitor).
- **Visual do JARVIS nos apps**: o navegador dos apps abre em modo escuro (sites sem tema escuro são escurecidos
  automaticamente) e o JARVIS aplica as cores da interface (azul-marinho, ciano, barra de rolagem fina) no WhatsApp,
  YouTube, Instagram, Facebook e Google (`tema_apps.py`); liga/desliga em Configurações.
- **Navegador do JARVIS**: a aba NAVEGADOR do painel APPS é um Chrome/Edge de verdade (Google) dentro da interface,
  para pesquisas manuais, com ◂ ▸ ↻ e barra de endereço/pesquisa; "pesquisa X aqui" abre a busca nele e o JARVIS
  consegue ler a página aberta para responder. Todo site aberto nele ganha o tom azul do JARVIS.
- **Apelidos e avisos sob controle**: "chama a Gabi Pedra Home Office MH de Gabi" (vale nos comandos e nos avisos);
  "para de me avisar da Gabi / do grupo X / do Instagram" e "volta a avisar..." (`avisos.py`, data/avisos.json).
  Conversas silenciadas no próprio WhatsApp já não geram aviso.
- **Navegador interno**: manchetes e links dos cards abrem dentro do JARVIS ("abre essa notícia" também). Modo
  LEITURA (o texto da notícia no visual do JARVIS) ou PÁGINA (o site original, sem scripts, num iframe isolado);
  ◂ volta, ⤢ amplia, ↗ abre no navegador do Windows. Links do Google Notícias viram o endereço real da notícia, e
  endereços da rede local/127.0.0.1 não são abertos (`leitor.py`).
- **Janelinha flutuante**: com o JARVIS minimizado ou na bandeja, uma barra no visual do JARVIS fica por cima das
  outras janelas (escrever, microfone, o núcleo animado com o estado, voz liga/desliga, abrir). Clique no núcleo
  para abrir o JARVIS; arraste a barra para mudar de lugar (a posição fica salva). Em cima dela aparecem também:
  a confirmação (SIM/NÃO, ou responda falando), o player do Spotify quando algo estiver tocando (botão ♪ esconde)
  e a letra sincronizada ("mostra a letra" ou o botão de letra do player).
  Notícias, tabelas e outros cards que ele mostrar também aparecem ali (links abrem no navegador interno).
  Cada peça pode ser arrastada pelo título para qualquer lugar da tela e lembra a posição (duplo clique no título
  devolve para junto da barra). Duplo clique no núcleo alterna o **modo discreto**: só o núcleo flutuando (as
  confirmações, a letra e os cards continuam aparecendo). Liga/desliga em
  Configurações. É uma janela do navegador sem moldura, recortada no formato das peças, com cantos chanfrados (`flutuante.py`), que
  conversa com a janela principal pelo servidor (`/ws/flutuante`).
- **WhatsApp**: "Jarvis, manda uma mensagem pra Marini dizendo que chego às 8" (ou em duas etapas: "manda
  mensagem pro grupo da família" → "o que devo dizer?" → "que vou atrasar"). O pedido vai direto para a ferramenta
  (não depende da IA) e **só envia depois do seu "sim"**. Com a **extensão do JARVIS** no Chrome/Edge
  (Integrações → WhatsApp → Preparar extensão → "Carregar sem compactação" → pasta
  `C:SERS<VOCê>APPDATAROAMINGJARVISEXTENSAO-WHATSAPP`, CRIADA AO ABRIR O JARVIS), ele usa **o WhatsApp Web que já está
  aberto e logado no seu navegador**, dentro da aba, sem roubar o foco (a extensão fala com o JARVIS só por
  `ws://127.0.0.1/ws/whatsapp`, e o JARVIS só aceita conexões de extensão ali). Sem a extensão, usa o
  **WhatsApp Web numa janela
  própria**, fora da tela (perfil `%APPDATA%\JARVIS\whatsapp-web`, QR code uma vez por PC), controlada pelo
  protocolo de depuração do navegador (só em 127.0.0.1 e só durante o envio): pesquisa o nome **dentro do seu
  WhatsApp** (contatos do celular e grupos, tolerando erros como "Marine" → "Marini"), confere o cabeçalho da
  conversa antes de digitar e confere que a mensagem saiu. Se houver dúvida entre duas conversas, ele pergunta.
  O modo "app do PC" existe, mas o WhatsApp Desktop não deixa ler a tela: nele só dá para mandar para números.
- **Só acesso local**: a API e o WebSocket do JARVIS recusam pedidos que não venham de 127.0.0.1/localhost
  (um site aberto no navegador não consegue comandar o JARVIS).
- **Status report no WhatsApp**: "manda o status report no grupo Supervisão" envia o último relatório do
  StatusRobot **exatamente como ele montou** (com a formatação de WhatsApp) e lembra o grupo — depois basta
  "manda o report". Quando o robô termina, o JARVIS avisa sozinho e já deixa o envio esperando o seu "sim".
- **Conversa contínua**: depois de cada resposta ele continua ouvindo alguns segundos (8 por padrão, AI Voice) sem
  precisar de "Jarvis"; dá para **interromper** enquanto ele fala (o que for parecido com a própria fala é tratado
  como eco das caixas de som e ignorado).
- **WhatsApp: ler mensagens**: "tenho mensagens?" lista as conversas não lidas (quem, prévia, quantas) sem abrir
  nenhuma — nada é marcado como lido; "o que a Marini mandou?" / "lê o grupo X" abre a conversa e resume as últimas.
- **Controle de apps** (UI Automation do Windows): o JARVIS abre um app, "lê" a janela (botões, menus, campos) e
  age pelo nome — clicar, digitar, atalhos (ctrl+s...), focar/minimizar/maximizar/fechar. Fechar janela, clicar em
  "Excluir/Enviar/Pagar..." e Alt+F4/Delete pedem confirmação; nunca digita em campo de senha.
- **Código formatado**: pedidos de código voltam em bloco com destaque e botão de copiar (a voz não lê o código).
- **Lembra o cérebro**: o último cérebro escolhido fica salvo (`JARVIS_BRAIN`) e é o que abre da próxima vez.
- **Integrações** (aba Integrações, tudo opcional): **Google Agenda** (endereço secreto iCal — compromissos com
  repetições; card Agenda e alerta 1 h antes), **Gmail/e-mail** (IMAP com senha de app, só leitura: não marca nada
  como lido), **GitHub** (token pessoal: notificações, seus PRs e os que esperam sua revisão), **Docker**
  (containers; parar/reiniciar pede confirmação), **Ollama** (modelos locais), **Groq** e **OpenRouter**
  (chaves de API), além de Spotify, ElevenLabs, MHnet Notifier e StatusRobotReport.
- **Painel em tempo real** (atualiza a cada 1,5 s): CPU (total, por núcleo, histórico), temperatura,
  RAM e swap, discos, rede (upload/download), GPU NVIDIA, bateria, uptime e top processos.
- **Núcleo central** animado que muda de cor e ritmo conforme o estado: em espera (ciano),
  ouvindo (verde, reage ao volume do microfone), processando (âmbar) e falando.
- **Conversa por voz**: botão de microfone (ou tecla Espaço) e **modo "Jarvis"** (escuta contínua com
  palavra de ativação). Respostas faladas em português.
- **Conversa por texto** no console inferior.
- **Controle do PC**: abrir aplicativos (busca no Menu Iniciar / `.desktop`), abrir sites, pesquisar na web,
  teclas de mídia, volume, bloquear tela, agendar/cancelar desligamento.
- **Spotify**: tocar música/playlist/artista/álbum, pausar, pular, voltar, aleatório, "o que está tocando", volume.
  Configurado dentro do app (⚙ → Spotify): Client ID/Secret validados com o Spotify e botão **Conectar conta**.
  Um **card de player** aparece no painel direito quando algo toca (capa, progresso, tocar/pausar, anterior,
  próxima, aleatório, repetir e volume). Detalhe técnico: em contas grátis e apps em modo de desenvolvimento a
  Web API aceita "tocar X" mas não toca; por isso o JARVIS abre a faixa no app do PC (com o Spotify parado) ou
  põe na fila e pula para ela (com algo tocando), e só diz "tocando" depois de confirmar com o Spotify.
  **PC sem o app do Spotify**: a música abre no Spotify Web Player (open.spotify.com); depois do primeiro play
  no navegador, o card e os comandos controlam ele. O card também controla o Spotify do celular, se for lá
  que estiver tocando. "Abra o Spotify/WhatsApp/Discord/Teams" abre a versão web quando o app não existe.
- **Massivas do trabalho (MHnet Notifier)**: "Jarvis, tem massiva em Ponta Grossa?" — o JARVIS lê, só para
  consulta, o snapshot que o MHnet Notifier mantém em `%LOCALAPPDATA%\MassivaNotifier\massivas.db`
  (massivas da planilha, quedas de PPPoE do Grafana e Avisos N2). Procura por cidade, sigla do concentrador
  ou OLT (com ou sem acento) e avisa se a lista está desatualizada (Notifier fechado). Outro caminho pode
  ser definido em `MHNET_DB`.
  "Abra o app de massivas" abre o próprio MHnet Notifier (`%APPDATA%\MHnet Notifier\MHnetNotifierLauncher.exe`).
- **Status report (StatusRobotReport)**: "Jarvis, gera o status report (com 2 faltas)" abre o robô numa janela
  de console própria e **preenche sozinho** o que o JARVIS sabe: modo (agora/programado e horário), Enter nos
  logins salvos, **eventos e afetados** (só as massivas da planilha — a aba do Grafana/PPPoE não conta), bots
  (✅, ou ❌ se você disser que falhou) e as faltas, se você disser. Isso é feito por um processo auxiliar que se
  liga ao console do robô (`AttachConsole`), lê a pergunta na linha do cursor e escreve a resposta no buffer de
  entrada (`WriteConsoleInput`) — o robô não foi alterado e você continua podendo digitar na janela (logins,
  código do e-mail). "Como foi o último status report?" lê o relatório em `Documentos\Relatorios MHNET`.
  O `.exe` do robô é procurado em Área de Trabalho, Documentos e Downloads (ou em `JARVIS_STATUSROBOT`).
- **Atualizações do trabalho**: massivas em aberto, Avisos N2 e se o status report do dia já saiu.
- **Cards de contexto**: atualizações do trabalho/massivas, notícias e previsão do tempo **trocam os cards de
  hardware** por cards do assunto (massivas com afetados e status, Avisos N2, status report do dia; manchetes
  com link; clima agora e próximos dias). Voltam pelo botão *◂ SISTEMA*, ao pedir o status do sistema ou após 5 min.
- **Letra sincronizada**: "Jarvis, mostra a letra" (ou o botão de letra no card do Spotify) abre um quadro no
  centro que destaca o verso atual acompanhando a música; clicar num verso pula para ele. Letras do LRCLIB
  (gratuito, sem chave); a letra só aparece na tela — o JARVIS não a lê em voz alta.
- **Saudação ao ligar o PC**: ao abrir, o JARVIS cumprimenta e pergunta se você quer as **principais notícias
  do dia** (Google Notícias), as **atualizações do trabalho** ou uma **música** — e já fica ouvindo a resposta.
- **Boas-vindas ao desbloquear a tela**: ao bloquear (Win+L) o JARVIS guarda as massivas em aberto; ao voltar
  (depois de 30 s ou mais) ele dá boas-vindas e conta o que mudou — massivas novas, resolvidas ou nenhuma
  mudança. O estado da sessão é lido com `WTSQuerySessionInformation` (API do Windows) a cada 2 s.
- **Chama você pelo nome** de vez em quando (⚙ → Você), alternando com o tratamento ("senhor").
- **Pesquisa e informações da internet** (grátis, sem chave): o JARVIS pesquisa (DuckDuckGo/Wikipédia), lê a
  página e **responde com o resultado**; **previsão do tempo** (Open-Meteo, usa a sua cidade automaticamente
  ou `JARVIS_CIDADE`); **teste de velocidade** da internet (download, upload, latência — servidores da Cloudflare).
- **Conversa contínua**: quando o JARVIS pergunta algo ou pede autorização, o microfone abre sozinho por ~10 s
  para a sua resposta — sem dizer "Jarvis" nem clicar no microfone.
- **Processos**: listar, filtrar e **encerrar com confirmação** (modal na tela ou dizendo "confirmar").
- **Arquivos**: analisar pastas, salvar e ler notas, gerar relatórios `.txt`.
- **Troca de cérebro em tempo real** (Claude / GPT / Gemini / Groq / OpenRouter / Ollama local / suas contas /
  Offline) sem perder a conversa.
- **Fallback automático**: se a IA cair (sem internet, cota), o modo offline assume.
- **Modo terminal** (`python -m jarvis cli`) para usar sem interface gráfica.

### Exemplos de comandos

```
status do sistema                     quais processos estão consumindo mais memória?
qual a temperatura da CPU?            encerre o processo chrome        → pede confirmação
abra o spotify                        toque Back in Black no Spotify
coloca a playlist Rock Clássico       pausa / próxima / volume em 30
abra github.com                       pesquise receita de bolo no youtube
analise a pasta ~/Downloads           anote que a prova é sexta
gere um relatório                     desligue o computador em 10 minutos
tem massiva em Ponta Grossa?          abra o app de massivas
gera o status report com 2 faltas     como foi o último status report?
mostra a letra                        fecha a letra
quais as notícias de hoje?            atualizações do trabalho
```

Com um cérebro de IA conectado, a conversa é livre ("Jarvis, meu PC está lento, o que pode ser?").

## 6. Arquitetura

```
 Navegador (interface)                          Servidor Python (FastAPI)
 ┌──────────────────────────┐   WebSocket   ┌───────────────────────────────────┐
 │ núcleo (canvas) · painéis│◄─────────────►│ server.py ── stats a cada 1,5 s   │
 │ voz: SpeechRecognition   │  chat/stats/  │      │                            │
 │      speechSynthesis     │  confirmação  │ assistant.py (histórico, fallback)│
 └──────────────────────────┘               │      │                            │
                                            │ brain/ claude · openai · gemini · │
                                            │        local (regex offline)      │
                                            │      │ tool calling               │
                                            │ tools.py (registro + confirmação) │
                                            │   ├─ system_monitor.py (psutil)   │
                                            │   ├─ os_control.py (SO)           │
                                            │   ├─ spotify.py (Web API)         │
                                            │   └─ report.py / logger.py        │
                                            └───────────────────────────────────┘
```

```
.
├── src/jarvis/          # código-fonte
│   ├── brain/           # cérebros (Claude, GPT, Gemini, offline)
│   └── web/             # interface (HTML, CSS, JS)
├── scripts/             # setup, execução e monitor (Bash e PowerShell)
├── tests/               # testes automatizados (pytest)
├── docs/                # documentação complementar
├── .env.example         # modelo de configuração
├── requirements.txt
└── README.md
```

## 7. Instalação e execução

### Linux (Ubuntu/Debian)

```bash
sudo apt install python3 python3-venv curl playerctl pulseaudio-utils   # playerctl/pactl são opcionais
./scripts/setup.sh          # cria .venv, instala dependências, cria .env e pastas com permissões
nano .env                   # (opcional) coloque sua chave do Claude, GPT ou Gemini
./scripts/run.sh start      # inicia em segundo plano e abre http://127.0.0.1:8000
```

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
notepad .env
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
```

O `run.ps1` abre a interface como aplicativo (janela sem barra de endereço) no Edge ou Chrome.

### Execução manual (qualquer SO)

```bash
pip install -r requirements.txt
PYTHONPATH=src python -m jarvis            # Windows PowerShell: $env:PYTHONPATH="src"; python -m jarvis
```

> **Use Chrome ou Edge**: o reconhecimento de voz (Web Speech API) não existe no Firefox.
> Ao abrir, clique em **ATIVAR J.A.R.V.I.S.** (o navegador exige um clique antes de liberar áudio) e
> permita o microfone.

### Versão aplicativo (JARVIS.exe)

Um único arquivo que roda em qualquer Windows 10/11, **sem instalar Python**:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1     # gera dist\JARVIS.exe (~21 MB)
```

- Ao abrir, o `.exe` sobe o servidor internamente e mostra a interface numa janela própria (Edge em modo
  aplicativo, que já vem no Windows), já ativada e com o **modo "Jarvis" ligado** (diga "Jarvis…").
- **Bandeja do Windows**: ícone do reator com *Abrir JARVIS*, *Iniciar com o Windows* e *Sair*. **Fechar a
  janela não desliga o JARVIS**: ele reabre a interface escondida (fora da tela e sem ícone na barra de
  tarefas) e continua **ouvindo "Jarvis" e falando** da bandeja. *Abrir JARVIS* (ou abrir o `.exe` de novo)
  mostra essa mesma janela, sem reiniciar. Minimizada, a janela também continua ouvindo (o Edge é aberto com
  opções que impedem o Windows de "congelar" a página em segundo plano). Só o *Sair* encerra tudo.
- **Iniciar com o Windows** (⚙ ou menu da bandeja): grava em `HKCU\...\Run` (sem administrador) e abre
  minimizado, já ouvindo.
- Configurações, chaves, notas, logs e relatórios ficam em `%APPDATA%\JARVIS` de cada usuário — o `.exe`
  pode ser copiado para outro PC (pendrive, nuvem) e cada pessoa configura as próprias contas.
- O Windows pode mostrar "O Windows protegeu o computador" porque o `.exe` não tem assinatura digital:
  clique em **Mais informações → Executar assim mesmo**.
- A voz clonada **local** (XTTS, ~4 GB) não vai dentro do `.exe`; nele use o ElevenLabs ou a voz do navegador.

### Usando as suas contas (assinaturas) em vez de chave de API

Em ⚙ **Configurações → Contas**, o JARVIS conecta o seu **Claude Pro/Max**, **ChatGPT Plus/Pro** ou
**conta Google** pelas ferramentas oficiais de linha de comando de cada empresa (Claude Code, Codex CLI
e Gemini CLI):

1. **INSTALAR** — abre uma janela que instala a CLI oficial pelo npm, na pasta do usuário. **Sem Node.js
   e sem permissão de administrador** (ex.: PC do trabalho), o JARVIS baixa o Node.js **portátil** oficial
   (zip do nodejs.org, ~30 MB) em `%LOCALAPPDATA%\JARVIS\node` e usa ele — nada é instalado no sistema.
   Confira se a política de TI da empresa permite usar essas ferramentas.
2. **CONECTAR** — abre o login oficial no navegador. A senha é digitada só no site da empresa; o JARVIS
   nunca vê senha nem token.
3. **USAR** — ativa a conta como cérebro.

Como funciona por dentro (`src/jarvis/brain/cli.py`): a cada pergunta o JARVIS chama a CLI em modo
não interativo, **com as ferramentas próprias dela desligadas** (`--tools ""` no Claude Code, `read-only`
no Codex, modo `plan` no Gemini) e numa pasta temporária vazia. As ferramentas do JARVIS são ensinadas
por texto: quando o modelo quer agir, responde `<ferramenta>{"nome": ..., "args": ...}</ferramenta>`, o
JARVIS executa (com as mesmas confirmações de sempre) e devolve o resultado. É mais lento que a API
(~5 s numa conversa, ~11 s quando usa ferramenta) e consome o limite de uso do seu plano.

> Esses planos são para uso pessoal; confira os termos de uso de cada serviço antes de automatizar.
>
> **Gemini pela conta Google**: em out/2026 o Google encerrou o "Sign in with Google" do Gemini CLI para
> contas pessoais ("no longer supported... migrate to Antigravity"). Continua valendo para contas de empresa
> (Workspace). Para uso pessoal, use a **chave de API gratuita do Gemini**.

### Configurando o cérebro

Edite o `.env` e preencha **uma ou mais** chaves. Sem nenhuma, o JARVIS roda no modo offline.

| Cérebro | Variável | Onde obter |
|---|---|---|
| Claude | `ANTHROPIC_API_KEY` | https://console.anthropic.com/ |
| GPT | `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| Gemini | `GEMINI_API_KEY` | https://aistudio.google.com/apikey (tem nível gratuito) |
| Local grátis | `OPENAI_BASE_URL=http://localhost:11434/v1` | [Ollama](https://ollama.com) com um modelo que suporte *tools* |

Os modelos são configuráveis (`ANTHROPIC_MODEL`, `OPENAI_MODEL`, `GEMINI_MODEL`). O cérebro pode ser
trocado a qualquer momento no seletor **CÉREBRO** no topo da tela, e o modelo em ⚙ **Configurações**,
que lista os modelos disponíveis para a sua chave.

**Gemini**: os modelos 3.x "pensam" antes de responder, o que pode levar mais de 20 s. O JARVIS usa
`GEMINI_THINKING=minimal` (resposta em ~2 s). Quando o modelo escolhido está sobrecarregado (HTTP 503),
no limite gratuito (429), lento demais ou foi aposentado (404), o JARVIS tenta automaticamente modelos
reserva (`gemini-3.5-flash`, `gemini-3.1-flash-lite`, `gemini-flash-lite-latest`) e evita o modelo que
falhou pelos próximos 5 minutos. Só se todos falharem ele cai no modo offline.

**Qual escolher?** Para conversa natural com uso de ferramentas, os três funcionam bem. Claude costuma ser
o mais consistente seguindo a personalidade e as regras de confirmação; Gemini Flash é o mais barato e tem
nível gratuito; GPT é uma boa alternativa se você já tem créditos na OpenAI.

### Voz pelo ElevenLabs (opcional, nuvem, plano pago)

A voz mais rápida (~1 s) e fiel. Em ⚙ **Configurações → Voz do JARVIS → ElevenLabs**, cole a chave de API:
o JARVIS lista as suas vozes clonadas e os modelos de fala; escolha e clique em **SALVAR VOZ** (a chave e
a voz são testadas antes de gravar no `.env`).

1. Em elevenlabs.io → **Voices → Instant Voice Clone**, envie 1–2 min da sua voz (ou o
   `data/voz/referencia.wav` gerado pelo `preparar_voz.py`) e confirme que a voz é sua.
2. Crie a chave em **API Keys** com permissão de *Text to Speech* e *Voices* (leitura).
3. Modelos: `eleven_flash_v2_5` (mais rápido, padrão) ou `eleven_multilingual_v2` (mais expressivo).
   O **SALVAR VOZ** testa a combinação (fala "Ok.") e mostra o motivo exato se o ElevenLabs recusar. O
   `eleven_v3` não aceita o contexto das frases vizinhas (HTTP 400); o JARVIS detecta e repete sem contexto.

Detalhes: cada frase é enviada com a anterior e a seguinte como contexto (entonação contínua);
frases repetidas ficam em cache para economizar créditos; se o ElevenLabs falhar (créditos esgotados,
sem internet, chave recusada) a fala sai na **voz clonada local** e, se ela também estiver fora, na voz
do navegador — com um aviso na tela.

### Voz clonada (opcional, local e gratuita)

O JARVIS pode falar com **a sua própria voz**, clonada a partir de uma gravação de 6 a 30 segundos,
usando o modelo **XTTS-v2** rodando 100% no seu computador (sem internet e sem custo depois de instalado).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_voz.ps1 -Audio "C:\caminho\minha_voz.mp3"
```
```bash
./scripts/setup_voz.sh ~/minha_voz.mp3
```

- O motor roda num **processo separado** (`voz/servidor_voz.py`, porta 8001) com um ambiente Python
  próprio em `~/.jarvis/venv-voz`, porque ainda não suporta o Python 3.14. O `run.ps1`/`run.sh` inicia e
  encerra esse processo junto com o JARVIS.
- A gravação é convertida por `voz/preparar_voz.py` (mono, sem silêncios, volume normalizado) e salva em
  `data/voz/referencia.wav`, que **não vai para o GitHub** (é um dado pessoal).
- A resposta é dividida em frases: enquanto uma toca, a próxima já está sendo gerada. O núcleo reage ao
  volume real do áudio.
- Em ⚙ **Configurações** escolha entre "Minha voz (clonada)" e "Voz do navegador". Se o servidor de voz
  estiver desligado, o JARVIS volta sozinho para a voz do navegador.
- **Desempenho na CPU**: a parte autorregressiva do modelo (GPT) é quantizada para **int8** ao carregar,
  o que a deixa ~1,7x mais rápida com pequena perda de qualidade. Num Ryzen 5 5600G sem placa de vídeo
  dedicada, cada frase leva de 2 a 5 s (cerca de 1,4 s de processamento por segundo de fala). Para usar o
  modelo original, mais fiel e mais lento, defina `JARVIS_VOZ_INT8=0`.
- O XTTS às vezes pronuncia o ponto final em português ("…senhor, ponto"); o servidor remove o ponto
  final antes de sintetizar.
- O modelo ocupa ~3-4 GB de RAM; com pouca memória livre o Windows passa a usar o disco e a voz fica
  várias vezes mais lenta.
- Licença do modelo: Coqui Public Model License — **somente uso não comercial**. Clone apenas a sua voz ou
  a de quem autorizou.

### Configurando o Spotify (opcional)

Sem configuração, o JARVIS abre a busca no app do Spotify e usa as teclas de mídia. Para tocar músicas
diretamente:

1. Acesse https://developer.spotify.com/dashboard e crie um app (marque **Web API**).
2. Em *Redirect URIs* cadastre exatamente `http://127.0.0.1:8888/callback`.
3. Copie *Client ID* e *Client Secret* para `SPOTIPY_CLIENT_ID` e `SPOTIPY_CLIENT_SECRET` no `.env`.
4. No primeiro comando de música, o navegador abre para você autorizar. O token fica em `data/.spotify_token`.

> Controlar a reprodução pela Web API exige conta **Spotify Premium** e o app aberto em algum dispositivo.

## 8. Scripts de automação

| Script | O que faz |
|---|---|
| `scripts/setup.sh` / `setup.ps1` | Verifica o Python, cria o ambiente virtual, instala dependências, cria `.env` e as pastas com permissões restritas |
| `scripts/run.sh {start\|stop\|restart\|status\|logs\|cli}` / `run.ps1` | Inicia o servidor em segundo plano (`nohup`), grava o PID em `run/jarvis.pid`, aguarda o *health check*, para com SIGTERM/SIGKILL, mostra o estado do processo com `ps` |
| `scripts/monitor.sh` / `monitor.ps1` | Monitor **independente do Python**: lê `/proc/stat`, `/proc/loadavg`, `free`, `df`, `ps`, `/sys/class/thermal`; grava `reports/monitor_AAAAMMDD_HHMMSS.txt`, registra alertas em `logs/alertas.log`, mantém os 30 relatórios mais recentes e retorna código de saída 1 se houver alerta |

Exemplos:

```bash
./scripts/monitor.sh --limite-cpu 80 --limite-mem 85
./scripts/run.sh status
```

Agendamento do monitor a cada 30 minutos com `cron` (`crontab -e`):

```cron
*/30 * * * * /caminho/para/jarvis/scripts/monitor.sh >> /caminho/para/jarvis/logs/cron.log 2>&1
```

Também é possível gerar o relatório pelo Python: `python -m jarvis relatorio`, ou pela voz: "gere um relatório".

## 9. Testes

```bash
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pytest -v
```

São **263 testes em 21 arquivos**. Eles rodam isolados (`conftest.py` cria pastas temporárias e desliga os
cérebros online), sem chamadas de rede reais (respostas simuladas) e sem enviar nada de verdade.

**Sistema Operacional (o foco da disciplina)**

- `test_system_monitor.py`: CPU, memória, discos e processos retornam valores coerentes; o processo do
  próprio teste aparece na lista; ordenação e filtro.
- `test_tools.py`: um processo filho é criado e **só é encerrado depois da confirmação**; recusar mantém o
  processo vivo; confirmação por voz ("sim"); processos protegidos são recusados; notas, análise de pasta,
  relatório e log de auditoria são gravados em arquivo.
- `test_apps.py`: controle de janelas de outros programas (UI Automation) e quais ações pedem confirmação.
- `test_massivas.py`: leitura **somente leitura** de um banco SQLite que outro programa está usando.
- `test_navegador.py`: escolha do navegador (Chrome, Edge de reserva) e perfis separados por janela.
- `test_web_e_spotify.py`: inclui a busca de aplicativos do menu Iniciar (apps da Microsoft Store antes de
  programas de terminal).

**Núcleo, IA e voz**

- `test_server.py`: endpoints HTTP, interface servida e conversa pelo WebSocket.
- `test_local_brain.py`: frases em português viram a ferramenta e os argumentos corretos (modo offline).
- `test_contas.py` e `test_gemini_fallback.py`: cérebros por conta (CLIs oficiais) e o Gemini contornando
  sobrecarga (503) e modelos aposentados (404).
- `test_voice.py`: voz do ElevenLabs com reserva na voz local.
- `test_cards_e_integracoes.py`, `test_trabalho.py`: agenda, GitHub, Docker, Gmail, notícias e falas
  espontâneas.

**Interface e integrações**

- `test_letra_e_paineis.py`, `test_letra_janela_cards.py`, `test_foco_flutuante.py`: cards, letra
  sincronizada, card em foco e a janelinha flutuante.
- `test_leitor.py`: navegador interno (modo leitura, modo página sem scripts) e o bloqueio de endereços da
  rede local.
- `test_embutidos.py`, `test_avisos_apelidos.py`: apps internos (WhatsApp, Instagram...), apelidos de
  contatos e avisos silenciados.
- `test_whatsapp.py`, `test_extensao_whatsapp.py`: escolha do contato certo (nunca arrisca a conversa
  errada), confirmação antes de enviar e a ponte com a extensão restrita a este computador.

### Verificação manual (roteiro de demonstração)

1. `./scripts/run.sh start` → `./scripts/run.sh status` mostra o PID do servidor.
2. Na interface, abra um programa pesado e veja a CPU/RAM subirem nos painéis.
3. Diga "Jarvis, quais processos estão consumindo mais memória?".
4. Diga "abra a calculadora" e depois "encerre o processo calc" (Windows: `CalculatorApp`; Linux:
   `gnome-calculator`) → confirme no modal → o processo some da lista.
5. "toque Back in Black no Spotify".
6. `./scripts/monitor.sh` → mostre o relatório em `reports/` e o `logs/acoes.jsonl`.
7. `./scripts/run.sh stop` → SIGTERM encerra o servidor.

## 10. Limitações conhecidas

- **Temperatura no Windows**: o Windows não expõe a temperatura da CPU para programas comuns. O JARVIS tenta
  o LibreHardwareMonitor (se estiver aberto) e o sensor ACPI (que costuma exigir administrador).
  GPUs NVIDIA são lidas pelo `nvidia-smi`. Em VMs/WSL normalmente não há sensores.
- **Voz**: depende da Web Speech API do Chrome/Edge, que envia o áudio para o serviço de reconhecimento do
  navegador (requer internet). A qualidade da voz falada depende das vozes pt-BR instaladas (no Edge, as
  vozes "Natural" soam muito melhores).
- **Volume no Windows** é ajustado por teclas virtuais (passos de 2%), sem dependências extras.
- **Spotify**: controle direto exige Premium; sem a Web API configurada, só abre a busca e usa teclas de mídia.
- **Modo offline** entende apenas comandos diretos (não conversa livremente).
- O servidor escuta apenas em `127.0.0.1` por segurança: não exponha a porta na rede, pois ele controla o PC.
- **Só no Windows**: a janelinha flutuante, os apps internos (WhatsApp, Instagram, Facebook, YouTube e
  Navegador), o controle de janelas de outros programas e o `JARVIS.exe` usam a API do Windows (Win32). No
  Linux funcionam o monitoramento, o controle de processos, os scripts `.sh` e a interface no navegador.
- **Apps internos**: cada site precisa de login uma vez (o WhatsApp pelo QR code). Se o WhatsApp
  desconectar o aparelho, o JARVIS não consegue ler nem enviar mensagens até um novo QR code ser escaneado.
- **WhatsApp**: a automação depende da estrutura da página do WhatsApp Web; se o site mudar, a busca de
  conversas pode parar de funcionar até o script ser ajustado.
- **Tema escuro nos apps** é aplicado por cima do site; algumas páginas do Navegador podem ficar com partes
  claras.

## 11. Possíveis melhorias

Palavra de ativação offline (Porcupine/Vosk), memória de longo prazo, lembretes com horário, controle de
casa inteligente, janelinha flutuante e apps internos também no Linux (X11/Wayland), instalador com
atualização automática.
