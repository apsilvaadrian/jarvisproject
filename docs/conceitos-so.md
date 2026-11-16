# Conceitos de Sistemas Operacionais no J.A.R.V.I.S.

Este documento liga cada conceito de Sistemas Operacionais usado no projeto a três coisas:

1. **onde ele aparece no código** (arquivo, linha e o trecho);
2. **o comando Linux equivalente**, para mostrar a mesma informação direto no terminal;
3. **perguntas prováveis** na apresentação, com a resposta curta.

A ideia central: o JARVIS não "inventa" nada. Ele pede ao **kernel** as mesmas informações que os comandos
`ps`, `top`, `free`, `df` mostram, e usa as mesmas chamadas de sistema que `kill` e `chmod` usam. No Python isso
passa pela biblioteca `psutil` (que no Linux lê `/proc` e `/sys`); nos scripts `.sh`, os arquivos do `/proc` são
lidos diretamente.

> Os números de linha valem para a versão atual do repositório. Se o código mudar, procure pelo nome da
> função citada.

## Resumo

| # | Conceito | Onde no projeto | Comando Linux |
|---|---|---|---|
| 1 | Processos (listar, estado, PID) | `system_monitor.list_processes` | `ps`, `top`, `pgrep` |
| 2 | Criação de processos | `os_control._spawn`, `scripts/run.sh`, `brain/cli.py` | `nohup`, `&`, `setsid`, `pstree` |
| 3 | Sinais e encerramento | `os_control.kill_process`, `run.sh stop`, `backup.sh` (`trap`) | `kill`, `kill -0`, `pkill` |
| 4 | CPU e carga | `system_monitor._cpu_percent`, `monitor.sh` (`/proc/stat`) | `top`, `uptime`, `nproc` |
| 5 | Memória | `system_monitor.memory_info`, `monitor.sh` (`/proc/meminfo`) | `free`, `cat /proc/meminfo` |
| 6 | Sistema de arquivos e disco | `system_monitor.disk_info`, `os_control.analyze_directory` | `df`, `du`, `stat` |
| 7 | Permissões | `setup.sh`, `os_control.save_note`, `monitor.sh`, `backup.sh` (`umask`) | `chmod`, `ls -l`, `umask` |
| 8 | Variáveis de ambiente | `config.py`, `run.sh` (`set -a`), `contas.comando` (`PATH`) | `env`, `export`, `echo $PATH` |
| 9 | Comunicação entre processos | `brain/cli.py` (pipes), servidor HTTP local | `\|`, `ss -ltnp`, `curl` |
| 10 | Logs e auditoria | `logger.py`, `logs/alertas.log`, `logs/backup.log` | `tail -f`, `journalctl` |
| 11 | Agendamento | `monitor.sh` e `backup.sh` no `cron`, `os_control.schedule_power` | `crontab`, `shutdown` |
| 12 | Concorrência e exclusão mútua | `threading.Lock` (`logger.py`, `system_monitor.py`), `flock` (`backup.sh`) | `flock` |
| 13 | Dispositivos (`/proc` e `/sys`) | `system_monitor._read_temperatures`, `monitor.sh` | `cat /sys/class/thermal/...` |

---

## 1. Processos

**O que é:** um processo é um programa em execução. O kernel dá a cada um um número único, o **PID**, e guarda
o estado dele (rodando, dormindo, zumbi...), o dono (usuário), quanto de CPU e memória usa e quem é o processo pai
(**PPID**). No Linux tudo isso fica visível em `/proc/<PID>/`.

**No JARVIS:** `src/jarvis/system_monitor.py:284` (`list_processes`) percorre todos os processos e monta a lista
que aparece na interface:

```python
attrs = ["pid", "name", "username", "memory_info", "cpu_percent"] + ([] if IS_WINDOWS else ["status"])
for p in psutil.process_iter(attrs):
    ...
    "estado": info.get("status", "running"),
    "memoria_mb": round(mem.rss / 1024**2, 1) if mem else 0.0,
```

O `scripts/monitor.sh` faz a mesma coisa só com comandos do Linux (os dois blocos "TOP 5 PROCESSOS"), e o
`scripts/run.sh status` mostra o processo do próprio JARVIS:

```bash
ps -o pid,ppid,stat,etime,%cpu,%mem,rss,cmd -p "$PID"
```

**Comandos equivalentes:**

```bash
ps -eo pid,user,stat,%cpu,%mem,rss,comm --sort=-%cpu | head -6   # os 5 que mais usam CPU
ps -eo pid,user,%mem,rss,comm --sort=-%mem | head -6             # os 5 que mais usam memória
top                                                              # o mesmo, atualizando a cada 3 s (q sai)
pgrep -a python                                                  # procura processos pelo nome
ls /proc/$(pgrep -f "jarvis --no-browser")/                      # o "arquivo" do processo no kernel
```

**Colunas que o professor pode perguntar:**

| Coluna | Significado |
|---|---|
| `PID` / `PPID` | número do processo / do processo pai |
| `STAT` | estado: `R` rodando, `S` dormindo (esperando algo), `D` esperando disco, `Z` zumbi, `T` parado. Letras extras: `l` = tem várias threads, `s` = líder de sessão, `+` = em primeiro plano no terminal |
| `%CPU` | fatia de CPU usada |
| `RSS` | memória física (RAM) realmente ocupada, em KB |
| `ETIME` | tempo desde que o processo começou |

**Pergunta provável:** *Por que o JARVIS aparece com `STAT` = `Sl`?*
Porque ele está **dormindo** (`S`), esperando uma requisição chegar pela rede, e tem **várias threads** (`l`):
o servidor web, o monitor de CPU, a vigia de avisos etc.

---

## 2. Criação de processos

**O que é:** no Linux, um processo cria outro com `fork()` (cópia de si mesmo) seguido de `exec()` (troca o
programa da cópia). O novo processo é **filho** de quem o criou. Se o pai morrer, o filho pode morrer junto
(quando recebe `SIGHUP` do terminal) ou pode ser "adotado" pelo `init`/`systemd`.

**No JARVIS, três formas:**

1. **Abrir aplicativos sem prender o JARVIS:** `src/jarvis/os_control.py:133` (`_spawn`)

   ```python
   kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "shell": shell, "cwd": str(Path.home())}
   ...
   kwargs["start_new_session"] = True     # Linux: o app vira líder de uma sessão nova (setsid)
   subprocess.Popen(cmd, **kwargs)
   ```

   `start_new_session=True` chama `setsid()` no filho: o app aberto (ex.: o navegador) não fica ligado ao
   terminal nem ao JARVIS, então fechar um não fecha o outro. A saída vai para `/dev/null`.

2. **Rodar o servidor em segundo plano:** `scripts/run.sh:50`

   ```bash
   nohup python3 -m jarvis --no-browser >"$OUTFILE" 2>&1 &
   echo $! >"$PIDFILE"
   ```

   - `&` roda em segundo plano (o shell não espera);
   - `nohup` faz o processo ignorar o `SIGHUP`, então ele sobrevive ao fechamento do terminal;
   - `$!` é o PID do último processo criado em segundo plano, guardado no **arquivo de PID** `run/jarvis.pid`.
     É assim que `stop` e `status` sabem qual processo controlar depois.

3. **Usar o Claude/ChatGPT da sua conta:** `src/jarvis/brain/cli.py:113` cria um processo filho a cada
   pergunta (`subprocess.run`) e espera ele terminar (veja o item 9).

**Comandos equivalentes:**

```bash
nohup sleep 300 > /dev/null 2>&1 &   # cria um processo em segundo plano
echo $!                              # PID dele
ps -o pid,ppid,sid,stat,cmd -p $!    # PPID = o shell; SID = sessão
setsid sleep 300 &                   # mesmo efeito do start_new_session=True
pstree -p $$                         # árvore de processos a partir do shell atual
```

**Pergunta provável:** *Para que serve o arquivo `run/jarvis.pid`?*
Para lembrar o PID do servidor entre execuções do script. O `run.sh start` grava, o `stop` lê e manda o sinal,
o `status` lê e chama `ps`. Sem ele, seria preciso procurar o processo pelo nome toda vez.

---

## 3. Sinais e encerramento de processos

**O que é:** sinais são avisos que o kernel entrega a um processo. Os principais:

| Sinal | Nº | O processo pode tratar/ignorar? | Uso |
|---|---|---|---|
| `SIGTERM` | 15 | sim | pedido educado para encerrar (fecha arquivos, salva estado) |
| `SIGKILL` | 9 | **não** | o kernel mata na hora, sem chance de limpar nada |
| `SIGINT` | 2 | sim | Ctrl+C no terminal |
| `SIGHUP` | 1 | sim | o terminal foi fechado (por isso o `nohup`) |
| `0` | — | — | não envia nada: só testa se o PID existe e se você tem permissão |

**No JARVIS:**

- **Encerramento controlado de qualquer processo:** `src/jarvis/os_control.py:373` (`kill_process`)

  ```python
  for p in procs:
      p.terminate()                                   # SIGTERM
  gone, alive = psutil.wait_procs(procs, timeout=timeout)   # espera até 3 s
  for p in alive:
      p.kill()                                        # SIGKILL só para quem não respondeu
  ```

  Antes disso, duas proteções:
  - `os_control.py:347` (`is_protected`) recusa PIDs ≤ 4, o próprio JARVIS e processos críticos como
    `systemd`, `init`, `Xorg`, `gnome-shell`, `sshd` (lista em `os_control.py:29`);
  - `tools.py:59` marca a ferramenta `encerrar_processo` com `confirm=True`: ela só executa depois que o
    usuário aprova na tela ou por voz.

- **Parar o servidor:** `scripts/run.sh:69` manda `kill -TERM`, espera 5 s testando com `kill -0`, e só então
  usa `kill -KILL` (linha 75). `run.sh:25` usa `kill -0` para saber se o processo ainda está vivo.

- **Limpar ao ser interrompido:** `scripts/backup.sh:46` usa `trap '...' EXIT`: se o script sair antes da hora
  (erro, Ctrl+C = `SIGINT`, `kill`), o Bash apaga o arquivo de backup pela metade.

**Comandos equivalentes:**

```bash
kill -l                 # lista todos os sinais
kill -TERM 1234         # o mesmo que "kill 1234"
kill -0 1234 && echo vivo || echo "não existe"
kill -KILL 1234         # o mesmo que "kill -9 1234"
pkill -TERM -f "jarvis --no-browser"   # por nome em vez de PID
```

**Pergunta provável:** *Por que não usar `kill -9` direto?*
Porque o `SIGKILL` não deixa o processo terminar o que estava fazendo: arquivos podem ficar pela metade e
conexões abertas. O `SIGTERM` dá a chance de fechar direito; o `SIGKILL` fica como último recurso.

---

## 4. CPU e carga do sistema

**O que é:** o kernel conta, para cada núcleo, quanto tempo a CPU passou em cada modo (usuário, sistema,
ociosa, esperando disco...). Esses contadores ficam em `/proc/stat` e só crescem. Para saber **o uso agora**,
lê-se duas vezes e calcula-se a diferença:

> uso % = 100 × (tempo total − tempo ocioso) ÷ tempo total, no intervalo entre as duas leituras

O **load average** (`/proc/loadavg`) é a média de processos rodando ou esperando CPU nos últimos 1, 5 e 15
minutos. Num computador de 4 núcleos, carga 4 = todos ocupados.

**No JARVIS:**

- `scripts/monitor.sh:41` (`cpu_usage`) faz a conta "na mão":

  ```bash
  read -r _ u1 n1 s1 i1 w1 q1 sq1 _ < /proc/stat
  sleep 1
  read -r _ u2 n2 s2 i2 w2 q2 sq2 _ < /proc/stat
  local idle=$(( (i2 + w2) - (i1 + w1) ))
  local total=$(( (u2+n2+s2+i2+w2+q2+sq2) - (u1+n1+s1+i1+w1+q1+sq1) ))
  echo $(( 100 * (total - idle) / total ))
  ```

- `src/jarvis/system_monitor.py:34` (`_busy_percent`) faz a mesma conta em Python, total e por núcleo, a partir
  de `psutil.cpu_times()`. O load average vem de `psutil.getloadavg()` (`system_monitor.py:65`).

**Comandos equivalentes:**

```bash
top                 # linha "%Cpu(s)": us = usuário, sy = sistema, id = ocioso, wa = esperando disco
uptime              # "load average: 1.36, 1.09, 1.20"
cat /proc/loadavg
nproc               # quantos núcleos
head -1 /proc/stat  # os contadores brutos que o monitor.sh lê
```

**Pergunta provável:** *Por que ler `/proc/stat` duas vezes?*
Porque os números são **acumulados desde o boot**. Uma leitura só diz quanto a CPU trabalhou desde que o
computador ligou, não agora.

---

## 5. Memória

**O que é:** o Linux usa a RAM sobrando como **cache de disco** (`buff/cache`) para acelerar leituras. Essa
memória é devolvida na hora em que um programa precisa. Por isso:

- **livre** (`MemFree`) = RAM sem uso nenhum, normalmente pouca;
- **disponível** (`MemAvailable`) = livre + o cache que pode ser devolvido. É o número que importa.

**Swap** é uma área no disco usada quando a RAM acaba. É muito mais lenta.

**No JARVIS:**

- `src/jarvis/system_monitor.py:80` (`memory_info`) calcula o uso real com o disponível:

  ```python
  vm = psutil.virtual_memory()
  "usado_gb": _gb(vm.total - vm.available),
  ```

- `scripts/monitor.sh:75` lê direto do `/proc/meminfo`:

  ```bash
  MEM="$(awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END {printf "%d", (t-a)*100/t}' /proc/meminfo)"
  ```

  Antes, o script lia a saída do comando `free` procurando a linha `Mem:`. Num Linux em português o `free`
  escreve `Mem.:`, então o uso de memória saía vazio e o alerta nunca disparava. Os nomes dentro do
  `/proc/meminfo` não mudam com o idioma (correção do commit `fix: uso de memória do monitor.sh em sistemas em
  português`).

**Comandos equivalentes:**

```bash
free -h                                          # colunas: total, usada, livre, buff/cache, disponível
grep -E 'MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree' /proc/meminfo
LANG=pt_BR.UTF-8 free | head -2                  # "Mem.:" (o motivo do bug)
LC_ALL=C free | head -2                          # "Mem:"
```

**Pergunta provável:** *O `free` mostra pouca memória livre. O computador está sem memória?*
Não necessariamente. O que conta é a coluna **disponível**: o cache entra como "usado", mas é devolvido
quando algum programa pede.

---

## 6. Sistema de arquivos e disco

**O que é:** cada partição tem um **sistema de arquivos** (ext4, vfat, ntfs...) e é **montada** numa pasta
(`/`, `/boot/efi`, `/media/pendrive`). Dentro dele, cada arquivo tem um **inode** com tamanho, dono, permissões
e datas, que a chamada de sistema `stat()` lê.

**No JARVIS:**

- `src/jarvis/system_monitor.py:93` (`disk_info`) lista as partições montadas e o espaço de cada uma, pulando
  sistemas de arquivos virtuais (`tmpfs`, `squashfs`, `overlay`), que não são disco de verdade:

  ```python
  for part in psutil.disk_partitions(all=False):
      if part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay"):
          continue
      usage = psutil.disk_usage(part.mountpoint)
  ```

- `src/jarvis/os_control.py:442` (`analyze_directory`) percorre uma pasta com `os.walk` e lê o tamanho de cada
  arquivo com `os.stat`. Pastas sem permissão não derrubam a análise: o `PermissionError` é contado e a busca
  segue.

  ```python
  for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
      ...
          size = os.stat(fp, follow_symlinks=False).st_size
      except (PermissionError, OSError):
          negados += 1
  ```

- `scripts/monitor.sh` usa `df -h` e alerta quando uma partição passa do limite (`--limite-disco`).

**Comandos equivalentes:**

```bash
df -h -x tmpfs -x devtmpfs -x squashfs   # espaço por partição (o mesmo filtro do código)
df -T                                    # mostra o tipo do sistema de arquivos
du -sh ~/Downloads                       # quanto uma pasta ocupa
du -ah ~/Downloads | sort -rh | head     # os maiores arquivos (o que analyze_directory faz)
stat README.md                           # o inode: tamanho, dono, permissões, datas
```

---

## 7. Permissões

**O que é:** cada arquivo tem permissões para três grupos de pessoas: **dono**, **grupo** e **outros**. Cada
um pode ter `r` (ler = 4), `w` (escrever = 2) e `x` (executar, ou entrar na pasta = 1). Somando, cada grupo
vira um dígito:

| Octal | Letras | Quem pode o quê |
|---|---|---|
| `600` | `rw-------` | só o dono lê e escreve |
| `640` | `rw-r-----` | dono lê e escreve; grupo só lê |
| `700` | `rwx------` | só o dono (pasta: só ele entra) |
| `750` | `rwxr-x---` | dono tudo; grupo entra e lê; outros nada |
| `755` | `rwxr-xr-x` | todos executam; só o dono altera (os scripts `.sh`) |

O **umask** é a máscara que o processo usa para *retirar* permissões de tudo o que ele cria. Com `umask 077`,
grupo e outros perdem tudo.

**No JARVIS:**

| Onde | O quê | Por quê |
|---|---|---|
| `scripts/setup.sh:34` | `chmod 600 .env` | tem chaves de API |
| `scripts/setup.sh:38` | `chmod 750 logs reports run` | o grupo pode ler os logs; outros não |
| `scripts/setup.sh:39` | `chmod 700 data` | notas e token do Spotify |
| `scripts/setup.sh:40` | `chmod +x scripts/*.sh` | permite rodar `./scripts/...` |
| `src/jarvis/os_control.py:489` | `os.chmod(path, 0o600)` | o arquivo de notas nasce privado |
| `scripts/monitor.sh:109` | `chmod 640 "$OUT"` | relatório legível pelo grupo |
| `scripts/backup.sh:29` | `umask 077` | backup (pasta 700, arquivo 600): contém dados pessoais |

Fora do código do JARVIS, as credenciais também usam `600`: `~/.git-credentials` (token do GitHub) e
`~/.claude/.credentials.json` (login do Claude).

**Comandos equivalentes:**

```bash
ls -l .env scripts/                      # mostra rw-------, rwxrwxr-x...
stat -c '%a %A %n' .env data logs        # em octal e em letras
chmod 600 .env
chmod u+x,go-rwx arquivo                 # forma simbólica
umask                                    # máscara atual do shell (geralmente 0002)
(umask 077; touch teste; ls -l teste)    # nasce rw-------
```

**Pergunta provável:** *Qual a diferença entre `chmod` e `umask`?*
O `chmod` muda a permissão de um arquivo que **já existe**. O `umask` define a permissão dos arquivos que **ainda
vão ser criados** por aquele processo e pelos filhos dele.

---

## 8. Variáveis de ambiente

**O que é:** todo processo recebe do pai uma lista de pares `NOME=valor` (o ambiente). Os programas leem dali
configurações como `PATH` (onde procurar comandos), `HOME`, `LANG` (idioma), `USER`. Um processo filho
**herda** uma cópia do ambiente do pai.

**No JARVIS:**

- **Toda a configuração vem do ambiente:** `src/jarvis/config.py:24` (`_load_dotenv`) lê o arquivo `.env` e
  joga cada linha em `os.environ`, sem sobrescrever o que já existe. Depois `config.py:40` (`_env`) lê de lá:

  ```python
  os.environ.setdefault(key, value)          # config.py:34
  ...
  return os.environ.get(name, default).strip()
  ```

  Exemplos: `JARVIS_PORT`, `JARVIS_BRAIN`, `ANTHROPIC_API_KEY`, `JARVIS_DATA_DIR`.

- **O shell exporta o `.env` para o processo filho:** `scripts/run.sh:14`

  ```bash
  set -a          # tudo o que for definido a partir daqui é exportado
  source .env
  set +a
  export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
  ```

- **`PATH`:** `src/jarvis/contas.py:142` usa `shutil.which("claude")`, que faz o mesmo que o comando `which`:
  procura o programa em cada pasta do `PATH`. Se `~/.local/bin` não estiver no `PATH` do servidor, o JARVIS diz
  que o Claude não está instalado, mesmo com o arquivo lá.

- **`LANG` muda a saída dos comandos:** foi a causa do bug de memória no `monitor.sh` (item 5).

- **`JARVIS_BACKUP_DIR`** muda o destino do `scripts/backup.sh` sem editar o script.

**Comandos equivalentes:**

```bash
env | sort | head                                   # ambiente do shell
echo "$PATH" | tr ':' '\n'                          # pastas onde os comandos são procurados
which python3 claude
export JARVIS_PORT=8001                             # vale para este shell e os filhos
JARVIS_BACKUP_DIR=/tmp/bk ./scripts/backup.sh       # só para este comando
tr '\0' '\n' < /proc/$(cat run/jarvis.pid)/environ | grep JARVIS   # ambiente do processo rodando
```

**Pergunta provável:** *Por que o script agendado no `cron` às vezes se comporta diferente do terminal?*
Porque o `cron` roda com um **ambiente mínimo**: `PATH` curto, sem `LANG`, sem o que está no `~/.bashrc`. Por
isso os scripts calculam a própria pasta (`ROOT=...`) e não dependem do `cd` nem do idioma.

---

## 9. Comunicação entre processos (IPC)

**O que é:** processos não enxergam a memória uns dos outros. Para conversar, usam mecanismos do SO:
**pipes** (a saída de um vira a entrada do outro), **sockets** (rede, inclusive local), arquivos, sinais.

**No JARVIS:**

- **Pipes com o Claude/ChatGPT da conta:** `src/jarvis/brain/cli.py:113`

  ```python
  subprocess.run(self.cmd + args, input=stdin, capture_output=True, text=True,
                 timeout=TIMEOUT, cwd=self.workdir, env=env)
  ```

  O texto da pergunta vai pela **entrada padrão (stdin)** do processo `claude`; a resposta volta pela **saída
  padrão (stdout)**. O processo roda numa pasta temporária vazia (`cwd`) e tem tempo máximo (`timeout`).

- **Sockets TCP locais:** a interface (navegador) conversa com o servidor em `127.0.0.1:8000` por HTTP e
  WebSocket. A voz clonada roda em **outro processo** em `127.0.0.1:8001`. O `run.sh` usa o endpoint `/health`
  para saber se o servidor subiu:

  ```bash
  curl -fs "http://$HOST:$PORT/health"      # {"status":"online","pid":...}
  ```

  `127.0.0.1` é a interface de *loopback*: os pacotes não saem do computador.

**Comandos equivalentes:**

```bash
ps -e | grep python | wc -l          # pipe: a saída do ps vira a entrada do grep
echo "oi" | claude -p                # o mesmo que o JARVIS faz por baixo (gasta o limite do plano)
ss -ltnp | grep 8000                 # qual processo está escutando na porta 8000
curl -s http://127.0.0.1:8000/health
```

---

## 10. Logs e auditoria

**O que é:** logs são o registro do que um programa fez, para depurar e auditar. Como crescem sem parar,
precisam de **rotação**: quando ficam grandes ou velhos, são renomeados e os mais antigos apagados.

**No JARVIS:**

- `src/jarvis/logger.py` cria dois registros em `logs/`:
  - `jarvis.log`, com **rotação por tamanho**: 1 MB por arquivo, mantendo 3 antigos (`jarvis.log.1`,
    `.2`, `.3`):

    ```python
    RotatingFileHandler(settings.log_dir / "jarvis.log", maxBytes=1_000_000, backupCount=3)
    ```

  - `acoes.jsonl`, de **auditoria**: uma linha JSON por ação executada no sistema (qual ferramenta, argumentos,
    se deu certo, data). Responde à pergunta "o que o JARVIS fez no meu computador?".
- `scripts/monitor.sh` grava alertas em `logs/alertas.log` e faz **rotação por quantidade** dos relatórios
  (mantém os 30 mais recentes, `monitor.sh:114`).
- `scripts/backup.sh` registra cada execução em `logs/backup.log` e faz rotação dos backups (item 11).

**Comandos equivalentes:**

```bash
tail -f logs/jarvis.log                   # acompanha o log em tempo real (Ctrl+C sai)
tail -n 5 logs/acoes.jsonl
grep ALERTA logs/alertas.log
journalctl -n 20                          # o log do próprio sistema (systemd)
ls /etc/logrotate.d/                      # como o Linux rotaciona os logs dos serviços
```

---

## 11. Agendamento de tarefas

**O que é:** o **cron** é um serviço (daemon) que acorda a cada minuto, lê as tabelas de agendamento
(`crontab`) e dispara os comandos na hora marcada. Formato: `minuto hora dia mês dia-da-semana comando`.

**No JARVIS:**

- `scripts/monitor.sh` foi feito para rodar no `cron`: devolve **código de saída** 1 quando há alerta, para uma
  automação poder reagir.
- `scripts/backup.sh` também: backup diário com rotação dos antigos.

  ```cron
  */30 * * * * /caminho/para/jarvis/scripts/monitor.sh >> /caminho/para/jarvis/logs/cron.log 2>&1
  0 2 * * *    /caminho/para/jarvis/scripts/backup.sh  >> /caminho/para/jarvis/logs/cron.log 2>&1
  ```

  `*/30` = a cada 30 minutos; `0 2 * * *` = todo dia às 2h00. O `>> ... 2>&1` junta saída e erros no fim de um
  arquivo, porque no cron não há terminal para mostrar nada.

- **Rotação dos backups** (`backup.sh:64`): o nome tem a data no formato `ano-mês-dia`, então a ordem
  alfabética é a mesma que a cronológica. Basta ordenar ao contrário e apagar a partir do (N+1)-ésimo.
- **Desligamento agendado:** `src/jarvis/os_control.py:513` (`schedule_power`) chama `shutdown -h +N`, e
  `cancel_power` chama `shutdown -c`.

**Comandos equivalentes:**

```bash
crontab -l                 # lista os agendamentos do seu usuário
crontab -e                 # edita
grep CRON /var/log/syslog  # confere se o cron disparou
shutdown -h +10            # desliga daqui a 10 minutos
shutdown -c                # cancela
```

---

## 12. Concorrência e exclusão mútua

**O que é:** quando duas threads ou dois processos mexem no mesmo recurso ao mesmo tempo, o resultado pode
ficar errado (**condição de corrida**). A solução é uma **trava** (*lock*): só quem tem a trava entra na
**seção crítica**.

**No JARVIS:**

- **Entre threads do mesmo processo:** o servidor tem várias threads. `src/jarvis/logger.py:18` cria um
  `threading.Lock()` para que duas ações gravadas ao mesmo tempo não misturem linhas no `acoes.jsonl`.
  `system_monitor.py:28` (`_cpu_lock`) protege a medição de CPU, que guarda a leitura anterior numa variável
  compartilhada.

  ```python
  with _lock:
      with open(settings.log_dir / "acoes.jsonl", "a", encoding="utf-8") as f:
          f.write(json.dumps(entry, ensure_ascii=False) + "\n")
  ```

- **Entre processos diferentes:** `scripts/backup.sh:35` usa `flock` num arquivo de trava. Se o `cron`
  disparar um backup enquanto o anterior ainda roda, o segundo desiste:

  ```bash
  exec 9>run/backup.lock                 # abre o arquivo no descritor 9
  flock -n 9 || { echo "Outro backup já está em andamento." >&2; exit 1; }
  ```

  Quando o processo termina, mesmo morto por `kill`, o kernel fecha os descritores e **solta a trava sozinho**.
  Nunca fica uma trava "presa".

- **Escrita atômica:** o `backup.sh` escreve num arquivo temporário e só no fim faz `mv` para o nome final
  (`backup.sh:58`). Na mesma partição, renomear só troca a entrada no diretório e é **atômico**: ninguém vê um
  backup pela metade.

**Comandos equivalentes:**

```bash
flock run/backup.lock -c 'sleep 30' &   # segura a trava por 30 s
./scripts/backup.sh                     # "Outro backup já está em andamento." (código 1)
ps -o pid,nlwp,cmd -p $(cat run/jarvis.pid)   # NLWP = número de threads do JARVIS
```

---

## 13. Dispositivos: `/proc` e `/sys`

**O que é:** no Linux, "tudo é arquivo". `/proc` e `/sys` são **sistemas de arquivos virtuais**: não existem no
disco, e o kernel gera o conteúdo na hora em que alguém lê. É por eles que se conversa com processos e
hardware (sensores, bateria, rede).

**No JARVIS:**

- `scripts/monitor.sh:90` lê a temperatura de cada sensor em `/sys/class/thermal/thermal_zone*/temp`
  (valor em milésimos de grau, por isso divide por 1000).
- `src/jarvis/system_monitor.py:191` (`_read_temperatures`) faz o mesmo com `psutil.sensors_temperatures()`,
  que lê `/sys/class/hwmon` e `/sys/class/thermal`.
- `/proc/stat`, `/proc/meminfo`, `/proc/loadavg` e `/proc/<PID>/` aparecem nos itens 1, 4 e 5.

**Comandos equivalentes:**

```bash
cat /sys/class/thermal/thermal_zone*/type       # nome de cada sensor
cat /sys/class/thermal/thermal_zone*/temp       # 50000 = 50 °C
ls -l /proc/meminfo                             # tamanho 0: o conteúdo é gerado na hora
cat /proc/cpuinfo | grep "model name" | head -1
```

---

## Roteiro rápido para demonstrar ao vivo

Com o JARVIS rodando (`./scripts/run.sh start`), na pasta do projeto:

```bash
./scripts/run.sh status                               # 1. processo: PID, PPID, STAT, memória
ps -eo pid,user,stat,%cpu,%mem,comm --sort=-%cpu | head -6
free -h && grep MemAvailable /proc/meminfo            # 5. memória: disponível ≠ livre
df -h -x tmpfs -x squashfs                            # 6. disco
ls -l .env && stat -c '%a %n' data logs               # 7. permissões 600 / 700 / 750
./scripts/monitor.sh --limite-mem 1                   # 4/5/10. relatório, alerta e código de saída 1
echo $?
./scripts/backup.sh --manter 3 && ls -l backups/      # 7/11/12. umask 077, rotação
./scripts/run.sh stop                                 # 3. SIGTERM
```
