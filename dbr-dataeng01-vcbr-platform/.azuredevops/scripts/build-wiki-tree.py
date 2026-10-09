#!/usr/bin/env python3
"""Monta a arvore de paginas do wiki a partir das pastas de documentacao do repo.

O script le a documentacao versionada e escreve, dentro de um clone do repo do
wiki, a subarvore que o pipeline administra. Ele nao fala com o Azure DevOps:
quem clona, commita e empurra e o publish-wiki.sh.

Mapeamento das origens:

    README.md                        -> <raiz>.md                (pagina de entrada)
    docs/**/*.md                     -> <raiz>/...
    solutions/<sol>/docs/README.md   -> <raiz>/<Solucao>.md      (na falta, solutions/<sol>/README.md)
    solutions/<sol>/docs/**/*.md     -> <raiz>/<Solucao>/...

O README.md de uma pasta de documentacao vira a pagina daquela pasta, e os
demais arquivos viram subpaginas dela. Os READMEs de pastas de codigo
(notebooks, sql, src, tests) ficam de fora de proposito: documentam o
repositorio, nao a plataforma.

Convencoes de wiki aplicadas (ver docs/publicacao-wiki.md):

    - o titulo da pagina vem do primeiro H1 do arquivo, nao do nome dele;
    - no nome do arquivo, hifen representa espaco no titulo;
    - uma pagina com subpaginas e um .md mais uma pasta de mesmo nome;
    - cada pasta recebe um .order definindo a sequencia das paginas;
    - links relativos entre documentos publicados sao reescritos para o
      caminho equivalente no wiki; links para arquivos de codigo passam a
      apontar para o navegador de arquivos do Azure Repos;
    - imagens (![...](arquivo.png)) sao copiadas para /.attachments do wiki,
      unico lugar de onde o wiki as exibe, e o link aponta para la.

Uso tipico:

    build-wiki-tree.py --repo . --wiki /tmp/wiki --root "Projetos-e-Iniciativas/Plataforma"
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# Marca todo arquivo gerado. Serve de trava: o script se recusa a sobrescrever
# uma pagina que exista no wiki sem esta marca, para nao destruir conteudo
# escrito a mao.
MARCA = "<!-- Gerado automaticamente a partir do repositorio. Nao edite pelo wiki. -->"

# Caracteres proibidos em nome de pagina pelo Azure DevOps.
PROIBIDOS = str.maketrans({"/": "-", "\\": "-", "#": None})

LINK = re.compile(r"(!?\[[^\]]*\])\(([^)\s]+)(\s+\"[^\"]*\")?\)")

# Pasta de anexos do wiki do Azure DevOps. Uma imagem so e exibida na pagina
# se estiver nela; um link para o navegador do Azure Repos aponta para uma
# pagina HTML, e o wiki mostra so o texto alternativo.
PASTA_ANEXOS = ".attachments"
EXTENSOES_DE_IMAGEM = {".png", ".jpg", ".jpeg", ".gif", ".svg"}
H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
# Remove apenas crase e asterisco do titulo. O sublinhado fica: faz parte de
# identificadores como vc_observability.pipeline_logging.
MARCACAO = re.compile(r"[`*]")

# O Azure DevOps limita a 235 caracteres o caminho completo da pagina, que
# inclui a URL do wiki. O prefixo depende da organizacao, do projeto e do nome
# do wiki; o padrao abaixo foi medido para
# https://grupovotorantim.visualstudio.com/VC-ANALYTICS/_wiki/wikis/VC-ANALYTICS.wiki/
LIMITE_AZDO = 235
PREFIXO_URL_PADRAO = 84


def log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class Pagina:
    origem: Path | None  # None para pagina sem arquivo de origem (stub)
    titulo: str
    filhos: list["Pagina"] = field(default_factory=list)
    nome: str = ""  # nome do arquivo, sem .md
    destino: PurePosixPath | None = None  # caminho dentro do clone do wiki


def titulo_do_arquivo(caminho: Path, alternativa: str) -> str:
    """Primeiro H1 do documento; se nao houver, usa a alternativa."""
    texto = caminho.read_text(encoding="utf-8")
    achado = H1.search(texto)
    if not achado:
        return alternativa
    return MARCACAO.sub("", achado.group(1)).strip()


def nome_de_pagina(titulo: str, usados: set[str]) -> str:
    """Converte um titulo em nome de arquivo valido e unico na pasta."""
    limpo = titulo.translate(PROIBIDOS)
    limpo = re.sub(r"\s+", " ", limpo).strip().strip(".")
    nome = limpo.replace(" ", "-") or "Pagina"

    candidato = nome
    sufixo = 2
    while candidato.casefold() in usados:
        candidato = f"{nome}-{sufixo}"
        sufixo += 1
    usados.add(candidato.casefold())
    return candidato


def arvore_de_pasta(pasta: Path) -> list[Pagina]:
    """Monta as paginas de uma pasta de documentacao.

    Arquivos .md diretos viram paginas folha, em ordem alfabetica. Cada
    subpasta com documentacao vira uma pagina, usando o README.md dela como
    conteudo quando existir.
    """
    paginas: list[Pagina] = []

    for arquivo in sorted(pasta.glob("*.md")):
        if arquivo.name.casefold() == "readme.md":
            continue
        paginas.append(Pagina(origem=arquivo, titulo=titulo_do_arquivo(arquivo, arquivo.stem)))

    for sub in sorted(p for p in pasta.iterdir() if p.is_dir()):
        filhos = arvore_de_pasta(sub)
        readme = sub / "README.md"
        if not filhos and not readme.exists():
            continue
        titulo = titulo_do_arquivo(readme, sub.name) if readme.exists() else sub.name
        paginas.append(
            Pagina(origem=readme if readme.exists() else None, titulo=titulo, filhos=filhos)
        )

    return paginas


def montar_arvore(repo: Path, titulo_raiz: str) -> Pagina:
    """Monta a arvore completa, da pagina de entrada as solucoes."""
    raiz = Pagina(
        origem=(repo / "README.md") if (repo / "README.md").exists() else None,
        titulo=titulo_raiz,
    )

    docs = repo / "docs"
    if docs.is_dir():
        raiz.filhos.extend(arvore_de_pasta(docs))

    solucoes = repo / "solutions"
    if solucoes.is_dir():
        for sol in sorted(p for p in solucoes.iterdir() if p.is_dir()):
            sol_docs = sol / "docs"
            # A pagina da solucao e a visao geral em docs/README.md; o
            # README.md da pasta da solucao documenta o codigo e so entra na
            # falta dela.
            readme = sol_docs / "README.md"
            if not readme.exists():
                readme = sol / "README.md"
            filhos = arvore_de_pasta(sol_docs) if sol_docs.is_dir() else []
            if not filhos and not readme.exists():
                continue
            titulo = titulo_do_arquivo(readme, sol.name) if readme.exists() else sol.name
            raiz.filhos.append(
                Pagina(origem=readme if readme.exists() else None, titulo=titulo, filhos=filhos)
            )

    return raiz


def nomear(pagina: Pagina, pasta: PurePosixPath, usados_pai: set[str]) -> None:
    """Define nome e caminho de destino de cada pagina, recursivamente."""
    pagina.nome = nome_de_pagina(pagina.titulo, usados_pai)
    pagina.destino = pasta / f"{pagina.nome}.md"

    usados: set[str] = set()
    for filho in pagina.filhos:
        nomear(filho, pasta / pagina.nome, usados)


def percorrer(pagina: Pagina):
    yield pagina
    for filho in pagina.filhos:
        yield from percorrer(filho)


def reescrever_links(
    texto: str,
    origem: Path,
    destino: PurePosixPath,
    mapa: dict[Path, PurePosixPath],
    repo: Path,
    url_repo: str,
    branch: str,
    avisos: list[str],
    anexos: dict[Path, str],
    prefixo_anexo: str,
) -> str:
    """Reescreve links relativos do documento para o equivalente no wiki.

    Imagens entram em `anexos` (origem -> nome do anexo) para o chamador
    copiar para a pasta de anexos do wiki.
    """
    pasta_destino = destino.parent

    def troca(m: re.Match) -> str:
        rotulo, alvo, titulo = m.group(1), m.group(2), m.group(3) or ""
        if re.match(r"^(https?:|mailto:|#|/)", alvo):
            return m.group(0)

        caminho, _, fragmento = alvo.partition("#")
        if not caminho:
            return m.group(0)

        try:
            absoluto = (origem.parent / caminho).resolve()
        except OSError:
            return m.group(0)

        fragmento = f"#{fragmento}" if fragmento else ""

        if absoluto in mapa:
            relativo = os.path.relpath(str(mapa[absoluto]), str(pasta_destino)).replace(os.sep, "/")
            return f"{rotulo}({relativo}{fragmento}{titulo})"

        if absoluto.exists():
            try:
                no_repo = absoluto.relative_to(repo).as_posix()
            except ValueError:
                avisos.append(f"{origem}: link fora do repositorio, mantido: {alvo}")
                return m.group(0)
            if rotulo.startswith("!") and absoluto.suffix.casefold() in EXTENSOES_DE_IMAGEM:
                nome = nome_de_anexo(prefixo_anexo, no_repo)
                anexos[absoluto] = nome
                return f"{rotulo}(/{PASTA_ANEXOS}/{nome}{titulo})"
            return f"{rotulo}({url_repo}?path=/{no_repo}&version=GB{branch}{titulo})"

        avisos.append(f"{origem}: destino inexistente, link mantido: {alvo}")
        return m.group(0)

    return LINK.sub(troca, texto)


def prefixo_de_anexo(raiz: PurePosixPath) -> str:
    """Prefixo dos anexos gerados, derivado da subarvore administrada.

    A pasta de anexos e compartilhada com as paginas escritas a mao. O
    prefixo identifica o que este script gerou, para poder apagar so isso.
    """
    return re.sub(r"[^A-Za-z0-9]+", "-", raiz.as_posix()).strip("-") + "--"


def nome_de_anexo(prefixo: str, no_repo: str) -> str:
    """Nome unico do anexo: o caminho no repositorio, achatado."""
    return prefixo + re.sub(r"[^A-Za-z0-9._-]+", "-", no_repo)


def conteudo_stub(pagina: Pagina) -> str:
    """Pagina de pasta sem README: indice simples das subpaginas."""
    linhas = [MARCA, "", f"# {pagina.titulo}", ""]
    for filho in pagina.filhos:
        linhas.append(f"- [{filho.titulo}]({pagina.nome}/{filho.nome}.md)")
    linhas.append("")
    return "\n".join(linhas)


def garantir_ancestrais(wiki: Path, raiz: PurePosixPath, nome_raiz: str) -> None:
    """Garante que os niveis acima da raiz existam e listem o proximo nivel.

    Nao sobrescreve nada: se a pagina do ancestral ja existe, fica como esta.
    Apenas acrescenta a entrada que falta no .order.
    """
    partes = list(raiz.parts)
    for i, parte in enumerate(partes):
        pai = wiki.joinpath(*partes[:i]) if i else wiki

        pagina_pai = pai / f"{parte}.md"
        if not pagina_pai.exists():
            pagina_pai.parent.mkdir(parents=True, exist_ok=True)
            titulo = parte.replace("-", " ")
            pagina_pai.write_text(f"{MARCA}\n\n# {titulo}\n", encoding="utf-8", newline="\n")
            log(f"  criada pagina do ancestral: {pagina_pai.relative_to(wiki).as_posix()}")

        ordem = pai / ".order"
        linhas = (
            [l.strip() for l in ordem.read_text(encoding="utf-8").splitlines() if l.strip()]
            if ordem.exists()
            else []
        )
        if parte not in linhas:
            linhas.append(parte)
            ordem.write_text("\n".join(linhas) + "\n", encoding="utf-8", newline="\n")
            log(f"  .order do ancestral atualizado: {ordem.relative_to(wiki).as_posix()} (+{parte})")

    # Entrada da propria raiz dentro do ancestral mais proximo
    pai = wiki.joinpath(*partes[:-1]) if len(partes) > 1 else wiki
    ordem = pai / ".order"
    linhas = (
        [l.strip() for l in ordem.read_text(encoding="utf-8").splitlines() if l.strip()]
        if ordem.exists()
        else []
    )
    if nome_raiz not in linhas:
        linhas.append(nome_raiz)
        ordem.write_text("\n".join(linhas) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True, help="raiz do repositorio de codigo")
    p.add_argument("--wiki", required=True, help="raiz do clone do repo do wiki")
    p.add_argument("--root", required=True, help="caminho da subarvore no wiki, ex.: Projetos-e-Iniciativas/Plataforma")
    p.add_argument("--repo-url", default="", help="URL do repo no Azure Repos, para links de codigo")
    p.add_argument("--repo-branch", default="main", help="branch usada nos links de codigo")
    p.add_argument(
        "--prefixo-url",
        type=int,
        default=PREFIXO_URL_PADRAO,
        help=f"tamanho da URL do wiki, usado no orcamento do limite de {LIMITE_AZDO} caracteres",
    )
    p.add_argument("--force", action="store_true", help="sobrescreve paginas sem a marca de gerado")
    p.add_argument("--dry-run", action="store_true", help="mostra a arvore e nao escreve nada")
    args = p.parse_args()

    repo = Path(args.repo).resolve()
    wiki = Path(args.wiki).resolve()
    raiz_rel = PurePosixPath(args.root.strip("/"))
    if not raiz_rel.parts:
        log("ERRO: --root vazio")
        return 2

    nome_raiz = raiz_rel.parts[-1]
    pasta_raiz = PurePosixPath(*raiz_rel.parts[:-1]) if len(raiz_rel.parts) > 1 else PurePosixPath()

    log(f"Repositorio: {repo}")
    log(f"Clone do wiki: {wiki}")
    log(f"Subarvore administrada: {raiz_rel}")

    arvore = montar_arvore(repo, nome_raiz.replace("-", " "))
    arvore.titulo = nome_raiz.replace("-", " ")
    nomear(arvore, pasta_raiz, set())
    # A pagina de entrada tem nome fixo, dado pelo --root
    arvore.nome = nome_raiz
    arvore.destino = pasta_raiz / f"{nome_raiz}.md"
    usados: set[str] = set()
    for filho in arvore.filhos:
        nomear(filho, raiz_rel, usados)

    paginas = list(percorrer(arvore))
    log(f"Paginas a publicar: {len(paginas)}")
    for pagina in paginas:
        origem = pagina.origem.relative_to(repo).as_posix() if pagina.origem else "(indice gerado)"
        log(f"  {pagina.destino}  <-  {origem}")

    if args.dry_run:
        log("DRY_RUN ativo. Nada escrito.")
        return 0

    alvo_md = wiki / f"{raiz_rel}.md"
    alvo_dir = wiki / raiz_rel
    if alvo_md.exists() and MARCA not in alvo_md.read_text(encoding="utf-8") and not args.force:
        log(f"##vso[task.logissue type=error]{raiz_rel}.md existe no wiki e nao foi gerado por este")
        log("script. Renomeie a pagina no wiki ou use --force para sobrescrever.")
        return 1

    # A subarvore e reconstruida do zero, para que documento removido do repo
    # desapareca do wiki. Nada fora dela e tocado.
    if alvo_dir.exists():
        shutil.rmtree(alvo_dir)
        log(f"Subarvore anterior removida: {raiz_rel}")

    # Mesma regra para as imagens: as geradas antes saem, para que uma imagem
    # removida do repo nao fique no wiki. As anexadas a mao nao tem o prefixo.
    pasta_anexos = wiki / PASTA_ANEXOS
    prefixo_anexo = prefixo_de_anexo(raiz_rel)
    if pasta_anexos.is_dir():
        for antigo in pasta_anexos.glob(f"{prefixo_anexo}*"):
            antigo.unlink()

    mapa = {p.origem.resolve(): p.destino for p in paginas if p.origem}
    avisos: list[str] = []
    erros: list[str] = []
    anexos: dict[Path, str] = {}
    escritos = 0

    for pagina in paginas:
        destino = wiki / pagina.destino
        destino.parent.mkdir(parents=True, exist_ok=True)

        if pagina.origem:
            texto = pagina.origem.read_text(encoding="utf-8")
            texto = reescrever_links(
                texto, pagina.origem.resolve(), pagina.destino, mapa, repo,
                args.repo_url, args.repo_branch, avisos, anexos, prefixo_anexo,
            )
            conteudo = f"{MARCA}\n\n{texto.lstrip()}"
        else:
            conteudo = conteudo_stub(pagina)

        destino.write_text(conteudo, encoding="utf-8", newline="\n")
        escritos += 1

        completo = f"{pagina.destino}"
        total = args.prefixo_url + len(completo)
        if total > LIMITE_AZDO:
            erros.append(
                f"caminho de {total} caracteres com a URL do wiki, acima do limite de "
                f"{LIMITE_AZDO} do Azure DevOps. Encurte o H1 do documento: {completo}"
            )
        elif total > LIMITE_AZDO - 30:
            avisos.append(
                f"caminho de {total} caracteres com a URL do wiki, a {LIMITE_AZDO - total} "
                f"do limite de {LIMITE_AZDO}: {completo}"
            )

        if pagina.filhos:
            ordem = destino.parent / pagina.nome / ".order"
            ordem.parent.mkdir(parents=True, exist_ok=True)
            ordem.write_text(
                "\n".join(f.nome for f in pagina.filhos) + "\n", encoding="utf-8", newline="\n"
            )

    garantir_ancestrais(wiki, raiz_rel, nome_raiz)

    if anexos:
        pasta_anexos.mkdir(parents=True, exist_ok=True)
        for origem_imagem, nome in anexos.items():
            shutil.copyfile(origem_imagem, pasta_anexos / nome)
    log(f"Imagens anexadas: {len(anexos)}")

    log(f"Arquivos escritos: {escritos}")
    for aviso in avisos:
        log(f"##vso[task.logissue type=warning]{aviso}")
    log(f"Avisos: {len(avisos)}")

    # Caminho acima do limite gera pagina inacessivel no wiki. Melhor falhar a
    # publicacao inteira e corrigir o H1 do que publicar algo quebrado.
    for erro in erros:
        log(f"##vso[task.logissue type=error]{erro}")
    if erros:
        log(f"Erros: {len(erros)}. Nada sera publicado.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
