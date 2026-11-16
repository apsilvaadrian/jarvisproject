# Verificação manual do J.A.R.V.I.S.

Execução do roteiro de demonstração do README (seção 9, "Verificação manual"), feita por **Gustavo Marini**
em 16/11/2026.

> Os prints da interface e dos terminais serão acrescentados em `docs/img/` num próximo commit.

## Ambiente

| Item | Valor |
|---|---|
| Sistema | Windows 11 Pro 10.0.26200 |
| CPU / RAM | AMD Ryzen 5 5600 (6 núcleos / 12 threads), 16 GB |
| Python | 3.12.10 (ambiente virtual em `.venv`) |
| Versão testada | commit `006862f` (`main`) |
| Cérebro | `JARVIS_BRAIN=auto` sem chaves de API → cérebro **Offline** (regras locais) |
| Scripts usados | `scripts\run.ps1` e `scripts\monitor.ps1` (equivalentes Windows de `run.sh` e `monitor.sh`) |

Os comandos de voz foram **digitados** na caixa "Falar com o JARVIS": o resultado é o mesmo, porque a voz só
transforma a fala em texto antes de chegar ao servidor.

## Resumo

| # | Passo | Resultado |
|---|---|---|
| 1 | Iniciar e ver o status | OK |
| 2 | CPU/RAM sobem nos painéis | OK |
| 3 | "quais processos estão consumindo mais memória?" | OK |
| 4 | Abrir e encerrar a calculadora com confirmação | OK |
| 5 | "toque Back in Black no Spotify" | Parcial (Spotify não conectado) |
| 6 | Monitor, relatório e log de ações | OK |
| 7 | Parar o servidor | OK |

## Passo a passo

### 1. Iniciar e ver o status

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 start
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 status
```

- Saída: `JARVIS online (PID 16208).` e o `status` mostrou o processo `python`, a hora de início e a memória.
- `run\jarvis.pid` contém `16208`. `GET /health` respondeu `{"status":"online","pid":16592}`.

**Observação: por que dois PIDs?** No Windows, o `python.exe` da `.venv` é só um **lançador**: ele cria um
processo filho com o Python de verdade, que é quem abre a porta 8000.

| PID | Pai | Executável | Memória |
|---|---|---|---|
| 16208 | (PowerShell) | `.venv\Scripts\python.exe` | 4 MB |
| 16592 | 16208 | `Python312\python.exe` | 76 MB |

O arquivo de PID guarda o pai. Isso não causa problema no `stop` (ver passo 7).

> Conceitos de SO: **criação de processos** e hierarquia **pai/filho** (PID e PPID); **arquivo de PID**
> para controlar um serviço em segundo plano; **variáveis de ambiente** (`PYTHONPATH`, lido do script).


### 2. CPU e memória nos painéis

Foi gerada uma carga artificial de 25 s (6 tarefas em laço de cálculo) enquanto a interface estava aberta.

- Uso da CPU: de **40%** para **96%**. O indicador de "saúde" caiu de 72 para 54 e voltou ao normal depois.
- O painel mostrou também o alerta real **"Disco C: 99% cheio"** (o C: desta máquina estava com 2 GB livres).

> Conceito de SO: **escalonamento de CPU**. Várias tarefas disputando os núcleos aparecem como uso alto
> medido pelo sistema (o JARVIS lê esses números com o `psutil`).


### 3. Processos que mais consomem memória

Mensagem: `quais processos estão consumindo mais memória?`

- O cérebro Offline converteu a frase na ferramenta `listar_processos` (`ordenar_por=memoria`, `limite=3`)
  e respondeu com os três maiores processos e o consumo em MB.

> Conceito de SO: **gerência de memória**. Cada processo tem o seu *working set* (memória física em uso),
> o mesmo valor que o Gerenciador de Tarefas e o `ps`/`top` do Linux mostram.


### 4. Abrir e encerrar a calculadora

Mensagens: `abra a calculadora` e depois `encerre o processo CalculatorApp`.

1. A calculadora abriu como `CalculatorApp` (PID 17868, 108,5 MB).
2. O JARVIS mostrou o modal **"Autorização necessária: Encerrar 1 processo(s) 'CalculatorApp.exe'
   (PIDs [17868], 108.5 MB)?"**.
3. Com o modal aberto, o processo **continuava vivo** (conferido com `Get-Process -Id 17868`).
4. Depois de **Confirmar**, o processo deixou de existir e a resposta foi
   `1 processo(s) 'CalculatorApp.exe' encerrado(s).`

> Conceitos de SO: **encerramento de processos** (no Windows, `TerminateProcess`; no Linux, os sinais
> `SIGTERM`/`SIGKILL`) e **controle de acesso**: uma ação destrutiva só acontece com autorização do usuário.


### 5. Spotify

Mensagem: `toque Back in Black no Spotify`

- **Parcial.** Sem `SPOTIPY_CLIENT_ID`/`SPOTIPY_CLIENT_SECRET` no `.env`, o JARVIS abriu a busca por
  "Back in Black" no aplicativo do Spotify e avisou: "Conecte o Spotify em ⚙ Configurações para eu dar o
  play". Não houve erro: ele usou o caminho reserva previsto para quando a integração não está configurada.

### 6. Monitor, relatório e log de ações

```powershell
powershell -ExecutionPolicy Bypass -File scripts\monitor.ps1
```

- Relatório gravado em `reports\monitor_20261116_102141.txt` (sistema, CPU, memória, discos e os 5 maiores
  processos por memória e por tempo de CPU).
- Alerta registrado em `logs\alertas.log`: `ALERTA Disco C: em 99% (limite 90%)`.
- **Código de saída 1**, porque houve alerta (sem alerta, sai com 0). É isso que permite a um agendador
  (Agendador de Tarefas ou `cron`) saber se algo deu errado.
- `logs\acoes.jsonl` registrou as ações dos passos 3 e 4, uma por linha, com data, ferramenta, argumentos e
  resultado:

```json
{"timestamp": "2026-11-16T10:20:31", "ferramenta": "listar_processos", "argumentos": {"ordenar_por": "memoria", "limite": 3}, "sucesso": true, "origem": "chat", "resultado": "ok"}
{"timestamp": "2026-11-16T10:20:40", "ferramenta": "abrir_aplicativo", "argumentos": {"nome": "calculadora"}, "sucesso": true, "origem": "chat", "resultado": "Abrindo calculadora."}
{"timestamp": "2026-11-16T10:21:09", "ferramenta": "encerrar_processo", "argumentos": {"alvo": "CalculatorApp"}, "sucesso": true, "origem": "chat", "resultado": "1 processo(s) 'CalculatorApp.exe' encerrado(s)."}
```

> Conceitos de SO: **sistema de arquivos** (relatórios com data no nome e rotação dos 30 mais recentes),
> **logs** de auditoria e **código de saída** de processo.


### 7. Parar o servidor

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 stop
```

- Saída: `Encerrando PID 16208 ...` e `JARVIS encerrado.`
- Depois: os PIDs **16208 e 16592** deixaram de existir, a porta 8000 foi liberada, o `/health` parou de
  responder e o `status` mostrou `JARVIS parado.`

**Por que o filho (16592) também morreu, se o script só encerra o pai?** No Windows, encerrar um processo
**não** encerra os filhos automaticamente. Mas o lançador da `.venv` coloca o filho num **Job Object** com a
opção "encerrar ao fechar": quando o pai termina, o Windows encerra todos os processos do *job*. É parecido
com encerrar um **grupo de processos** no Linux.

**Diferença em relação ao README:** o README diz que o `stop` usa SIGTERM, o que vale para o `run.sh`
(Linux). No Windows, o `Stop-Process` chama `TerminateProcess`, que encerra na hora, sem dar ao programa a
chance de terminar de forma organizada.


## Testes automatizados (mesmo dia, mesma máquina)

`.venv\Scripts\python -m pytest -q` → **262 passaram, 1 falhou** (cerca de 5 min).

- O teste que falhou foi `test_gemini_fallback.py::test_limite_por_minuto_espera_e_repete_o_mesmo_modelo`.
  Ele passa sozinho e passa quando o arquivo dele roda inteiro; só falha na suíte completa.
- Causa: o `test_embutidos.py` deixa rodando uma *thread* que chama `time.sleep(0.12)` em laço. O teste do
  Gemini troca o `time.sleep` por uma função falsa para medir as esperas, mas o módulo `time` é o mesmo para
  o processo inteiro. A outra *thread* passa a usar a função falsa e enche a lista de esperas com `0.12`.
- Conceito de SO: **threads compartilham a memória do processo**. Trocar algo num módulo vale para todas.
- Situação: pendente. O teste é do código do Adrian, que vai decidir a correção.
