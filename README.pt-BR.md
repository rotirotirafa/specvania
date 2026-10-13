# specvania

[English](README.md) · [Português brasileiro](README.pt-BR.md)

## O que é

**Sua spec vira um castelo metroidvania. Os agentes programam; você dirige o grupo.**

O Mago transforma sua ideia e as respostas da etapa Clarify em um mundo, o
Guerreiro executa suas salas e o Paladino revisa cada checklist e enfrenta os
cenários do chefe de cada área. Áreas viram andares, dependências viram portas
e salas concluídas viram commits locais. O registro guarda features, versões
da spec, execuções e bugs. Abra uma câmara para ler missão, código, diff e revisão.

O jogo desktop, o estúdio, o castelo, o painel Monaco somente para leitura e
Ver demo offline estão implementados em inglês e português brasileiro.
**Ainda não foi lançado — o pacote no PyPI e os builds para download chegam
com o primeiro lançamento.** A demo incluída é sintética; uma gravação de uma
execução real vai substituí-la.

## Instalação

Instale primeiro a CLI Python (Python 3.12+, Git e [uv](https://docs.astral.sh/uv/)),
depois o app desktop. O app não inclui Python. Os comandos a partir de um clone
funcionam hoje; o nome do pacote no PyPI e os artefatos para download só estarão
disponíveis **após o primeiro lançamento**.

A partir do clone do repositório:

```sh
uv tool install --compile-bytecode .
```

Após o lançamento no PyPI:

```sh
uv tool install --compile-bytecode specvania
uvx specvania --version
```

`uvx specvania` executa a CLI sem instalação permanente; o app precisa da
instalação persistente de `uv tool install` no PATH do shell de login (ou do
caminho do executável em Configurações). Para expedições reais, exporte a chave
do provedor no perfil lido por esse shell, por exemplo `~/.zshrc`:

```sh
export NVIDIA_API_KEY='<sua chave>'
```

Depois escolha **Reiniciar estúdio** na tela de erro ou reabra o app.
A tela de primeiro uso explica a instalação quando a CLI não executa. Ver
demo permanece visível e fica habilitado após iniciar a CLI; **não precisa de
chave de API**. Um erro de chave ausente ainda permite assistir à demo.

Baixe o app em [GitHub Releases](https://github.com/rotirotirafa/specvania/releases)
quando a v0.3.0 for publicada. Veja **Aplicativo desktop opcional** abaixo para abrir os
builds sem assinatura no macOS.

## Como jogar

1. Abra o app pelo Dock para chegar à **tela de título**; escolha EN ou PT.
2. Escolha **Novo projeto** ou **Abrir projeto** e selecione uma pasta Git com
   um commit em HEAD e autor Git configurado. Inicialize specvania se oferecido.
   **Verificar o grupo** testa os modelos configurados e precisa da chave do provedor.
3. Inicie uma nova expedição e digite sua **ideia**. Responda às perguntas de
   **Clarify** do Mago; Enter aceita o padrão. O castelo revela seu mundo.
4. Escolha **Salvar mapa** para criar o commit do mundo e **Iniciar expedição**.
   Se outros arquivos tiverem alterações, cancele ou inicie mesmo assim explicitamente.
5. Dirija a expedição: aprove comandos, escolha Tentar novamente / Pular / Abortar
   quando o grupo cair, ou Descansar / Abortar quando a stamina acabar. Clique
   numa câmara para abrir o **painel da sala**: Missão, Código, Diff e Revisão.
   O Monaco é somente para leitura.
6. Acompanhe o Paladino contra o chefe de cada andar. A **tela final** mostra
   salas, tokens, chamadas, trocas de modelo, tempo e moedas quando houver
   preços, com Ver commits e Voltar ao castelo.

**Ver demo** na tela de título reproduz a gravação incluída offline, sem
chave de API, projeto ou conexão com o provedor, a 1×, 2× ou 4×, com pausa.
Mostra as respostas de Clarify e aprovações gravadas. Precisa da CLI instalada,
como o restante do jogo. A gravação atual é sintética.
A interface e seus arquivos de arte, fonte, Phaser e Monaco carregam localmente;
o planejamento e a programação ao vivo contatam o provedor configurado.

## Início rápido em Python

Você precisa de Python 3.12 ou superior, Git e uv. O pacote Python usa SQLite
por padrão e não precisa de Docker nem Node. Em um clone novo:

```sh
cd specvania
uv sync --locked
uv run specvania --help
uv run specvania replay demo
```

`replay demo` abre o mapa com uma execução **sintética** incluída; não precisa de
configuração, chave de API ou conexão com o provedor. Encerre com Ctrl+C no
terminal. Veja a [origem da demo](src/specvania/examples/demo/README.md).
Para disponibilizar a CLI em seus projetos, instale a partir do clone:

```sh
uv tool install --compile-bytecode .
```

Após a publicação do pacote no PyPI, execute sem instalação permanente:

```sh
uvx specvania replay demo
```

Para planejar e executar, entre em um repositório Git com commit em HEAD e
autor Git configurado. Defina a chave do provedor no ambiente e execute:

```sh
specvania init
export NVIDIA_API_KEY='<sua chave>'
specvania doctor
git add .gitignore .specvania/config.toml
git commit -m "configure specvania"
specvania plan "add greeting"
specvania lint .specvania/specs/add-greeting
git add .specvania/specs/add-greeting
git commit -m "specify greeting"
specvania run .specvania/specs/add-greeting --ui=browser
specvania history add-greeting
specvania status
specvania export <run-id>
specvania replay <run-id>
```

O Mago normalmente faz perguntas de Clarify; Enter aceita o padrão. Use
`--no-clarify` para pular essa etapa. Um mundo escrito à mão (`spec.md` +
`tasks.md`) também funciona após passar em `specvania lint <spec-dir>`.
Faça commit do mundo antes de executar: `run` exige a árvore limpa, salvo uso
explícito de `--allow-dirty`. `export` grava `.specvania/runs/<run-id>/events.jsonl`;
replay também aceita esse arquivo. Os resumos ficam ao lado, em `summary.md`.

## SDD ↔ jogo

| Conceito SDD | specvania | No mapa |
|---|---|---|
| Spec (`spec.md` + `tasks.md`) | Mundo | O castelo inteiro |
| Grupo de tarefas / marco | Área | Um andar do castelo |
| Tarefa | Sala | Uma câmara de pedra, escura e trancada até suas portas abrirem |
| `depends_on` | Porta | Abre quando todas as dependências estiverem concluídas |
| Checklist passa na revisão | Sala concluída | Câmara acende, baú abre, commit é criado |
| Cenários atribuídos à área | Chefe | Arena no fim do andar; um segmento de HP por cenário |
| Planejador / Programador / Revisor | Mago / Guerreiro / Paladino | O grupo |
| Orçamento de tokens da execução | Stamina | Barra do grupo no HUD; tokens gastos por personagem |
| Espera após 429 | Recarga | Personagem descansa; contador visível |
| Revisor rejeita | Respawn | Volta à entrada da sala com feedback |
| Máximo de tentativas por sala | Vidas | Corações no HUD |
| Custo equivalente | Moedas | Apenas para modelos com preços configurados |
| Bug aberto | Monstro | Na sala e no Bestiário |
| Histórico do projeto | Registro | O arquivo de save |

## Papéis e cadeias de modelos

O provedor padrão é o endpoint compatível com OpenAI do NVIDIA NIM,
`https://integrate.api.nvidia.com/v1`. Edite `.specvania/config.toml` para mudar
`provider.base_url`, o nome da variável da chave ou as cadeias de modelos de
cada papel. Cada papel usa o primeiro modelo saudável e troca após falhas
repetidas de 429/5xx, ou imediatamente em HTTP 404. O grupo compartilha um
limitador por chave (40 RPM por padrão). `provider.timeout_s` define o tempo
limite em segundos (número positivo finito; padrão 300).

Os padrões são um **retrato do catálogo de 2026-10-12, sem qualidade validada**.
Um modelo listado no catálogo pode estar indisponível ou não responder.
`doctor` verifica os ids; `doctor --probe` faz uma chamada mínima
por modelo distinto. Ambos precisam da chave e contatam o endpoint configurado.

| Papel | Trabalho | Cadeia padrão, em ordem |
|---|---|---|
| Mago | Clarify, spec e salas | `moonshotai/kimi-k3` → `z-ai/glm-5.3` → `nvidia/nemotron-3-ultra-550b-a55b` |
| Guerreiro | Executar uma sala por ações JSON | `z-ai/glm-5.3` → `moonshotai/kimi-k2.6` → `deepseek-ai/deepseek-v4.1-flash` |
| Paladino | Revisar checklists e avaliar chefes | `nvidia/nemotron-3-ultra-550b-a55b` → `deepseek-ai/deepseek-v4.1-flash` → `z-ai/glm-5.3-flash` |

## Aplicativo desktop opcional

O shell Electron em [apps/desktop](apps/desktop/README.md) inicia o estúdio
Python pelo shell de login e abre a tela de título. Oferece controles de
aprovação e notificações nativas. Instale o dmg macOS (`arm64` ou `x64`) ou o
AppImage Linux da versão correspondente quando os artefatos estiverem disponíveis.
Os builds são **sem assinatura** e **não notarizados** no macOS.
Abra o dmg da arquitetura do seu Mac (arm64 para Apple Silicon, x64 para Intel)
e arraste `specvania.app` para `/Applications`. Para um build em que confia,
clique com o botão direito no Finder e escolha **Abrir**, depois confirme.
Se o macOS ainda bloquear, após a tentativa abra **Ajustes do Sistema →
Privacidade e Segurança → Abrir Mesmo Assim**. No Linux, torne o AppImage
baixado executável e abra-o. Compilar o app exige Node/npm; usar Python com
navegador não.

O launcher procura `desktop.path` na configuração, depois `SPECVANIA_DESKTOP`,
`specvania-desktop` (ou AppImage Linux) no PATH e `/Applications/specvania.app`
no macOS. `specvania run <spec-dir> --ui=auto` prefere o app instalado e avisa
quando recorre ao navegador. `--ui=desktop` exige o app; `--ui=browser` abre uma
aba somente de visualização. `replay demo` também usa a seleção automática.

## Postgres opcional para equipes

SQLite em `.specvania/ledger.db` é o registro padrão. Para uso em equipe, instale
o extra `postgres` e inicie o único serviço opcional a partir do clone:

```sh
uv tool install --compile-bytecode --force '.[postgres]'
docker compose up -d --wait postgres
export SPECVANIA_DATABASE_URL='postgresql+psycopg://specvania:specvania@localhost:5432/specvania'
```

Execute os mesmos comandos `init`, `plan`, `run`, `history` e `replay` no projeto
com essa variável definida. Migrações são automáticas. A variável seleciona o
registro; não copia o histórico SQLite para Postgres. As credenciais do compose
são apenas para desenvolvimento local e a porta usa loopback. Remova
`SPECVANIA_DATABASE_URL` para voltar ao SQLite. Execute `docker compose down`
no clone ao terminar; Redis não é necessário.

## Segurança

- **Git:** `run` exige árvore limpa salvo `--allow-dirty`, cria
  `specvania/<run-id>` a partir de HEAD, faz um commit por sala concluída e nunca
  faz push. Revise a branch local antes de publicar.
- **Comandos:** todo comando de shell proposto pelo modelo exige aprovação
  explícita no terminal ou no app. A primeira resposta vence e cancela a outra
  pergunta. Os `gates` escritos pelo usuário na configuração executam sem pedir
  aprovação. Comandos executam localmente com suas permissões, sem contêiner.
- **Arquivos:** o contexto dos modelos ignora `.git/`, caminhos ignorados e
  padrões de segredos (`.env*`, `*.pem`, `*.key`, `id_*`). As ferramentas de arquivo
  resolvem links simbólicos e não leem nem escrevem fora da raiz do repositório.
  Essas restrições não limitam um comando de shell aprovado.
- **Interface local:** o servidor escuta em `127.0.0.1`. Um token de
  visualização aleatório é exigido para a página e o WebSocket, que verifica
  Origin. A aba do navegador só observa. Mensagens de controle do desktop exigem
  outro token de controle. Mantenha os URLs de visualização privados.
- **Abertura pelo Dock:** Electron inicia `specvania studio` por
  `$SHELL -lic`. Exporte a chave do provedor no perfil lido pelo shell de login
  (por exemplo `~/.zshrc`): a chave nunca passa pelo Electron. O estúdio devolve
  porta e tokens em um handshake no stdout; no modo estúdio os tokens nunca vão
  para argv, arquivos ou logs. Configurações permite definir o caminho da CLI.
- **Desktop iniciado pela CLI:** `run --ui=desktop` ainda passa o token de
  controle como argumento do processo (`--control-token`), visível na inspeção
  local de processos. Esse caminho não usa o handshake do estúdio. Tokens ficam
  fora do registro, dos eventos exportados e dos logs; o token de controle fica
  fora dos URLs.
- Chaves de API ficam em variáveis de ambiente; a configuração guarda só seus
  nomes. Segredos registrados são rejeitados nos eventos e relatórios. Passos,
  vidas e tamanhos de observações têm limites configuráveis.

## Adicionar preços de referência

Nenhum preço é incluído. Os resumos sempre mostram tokens. Para mostrar
moedas, crie `.specvania/pricing.toml` com uma tabela `[[prices]]` por modelo.
Substitua cada marcador pelo id do modelo, taxas publicadas não negativas
**por milhão de tokens**, moeda, URL da fonte e data de consulta antes de salvar;
isto é um modelo de configuração, não uma cotação:

```toml
[[prices]]
model = "<model id>"
input_per_mtok = <published input rate>
output_per_mtok = <published output rate>
currency = "<currency>"
source_url = "<https://source-of-the-price>"
checked_on = <YYYY-MM-DD>
```

`specvania status <run-id>` usa o arquivo. Moedas aparecem apenas para modelos
com preço, com `source_url` e `checked_on`; os demais mostram `not priced`.
As taxas representam uma referência mantida pelo usuário, não a fatura do provedor.

## GIF de uma execução real

**Espaço reservado para GIF — uma gravação de um ciclo real plan → run aparecerá aqui.**
A demo incluída é sintética; uma gravação de uma execução real vai substituí-la.

## Créditos

- Kenney Tiny Dungeon — CC0 1.0; castelo, grupo e monstros.
- Press Start 2P — SIL Open Font License (OFL) 1.1.
- Phaser — MIT; desenho do castelo.
- Monaco — MIT; código e diff somente para leitura.
- Electron — MIT; shell desktop (inclui também os avisos de terceiros do runtime).

Versões, origem e licenças: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Contribuições e licenças

Explore a [spec](docs/spec.md) e as [salas](docs/tasks.md).
Leia [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) e o
[protocolo de execução](.agent/execution-protocol.md). Contribuições seguem o
mesmo contrato SDD que specvania executa.

[MIT](LICENSE). A arte do castelo vem de Kenney Tiny Dungeon (CC0), creditado acima.
Sem afiliação com a NVIDIA.
