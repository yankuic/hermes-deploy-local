# Hermes sandbox image — durable packages for the Docker terminal backend.
#
# Built on the default sandbox base so runtime behavior is unchanged (Debian
# trixie, root user, no s6 init). Bake system + user packages here so they
# survive container recreation / reaping (the image's writable layer does not).
#
# LaTeX: texlive-xetex provides the xelatex engine and 15/17 of the Awesome CV
# packages. fontawesome + sourcesanspro come from CTAN directly (NOT
# texlive-fonts-extra, which is ~624 MB download / 1.7 GB installed — the only
# Debian source for those two .sty files). Both packages' OpenType fonts are
# also registered with fontconfig because awesome-cv.cls loads them by name
# (\setfontfamily{\FA}{FontAwesome}, \setsansfont{SourceSans3}), and XeTeX
# resolves name-based lookups via fontconfig, not kpsewhich.
FROM nikolaik/python-nodejs:python3.11-nodejs20

# xelatex engine + the Awesome CV packages that ship in Debian texlive
# (fontspec, unicode-math, array, enumitem, ragged2e, geometry, fancyhdr,
# xcolor, ifxetex, etoolbox, setspace, tcolorbox, parskip, hyperref, xifthen).
# texlive-fonts-recommended supplies pzdr (ZapfDingbats), needed by hyperref
# link glyphs in the CV build.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        texlive-xetex \
        texlive-fonts-recommended \
    && rm -rf /var/lib/apt/lists/*

# ---- fontawesome (CTAN, v4.6.3.1 — same package Debian would ship) ----
# Classic source-layout zip; place each file type into its TDS directory.
RUN curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
        -o /tmp/fontawesome.zip \
        "https://mirrors.ctan.org/fonts/fontawesome.zip" && \
    cd /tmp && unzip -q fontawesome.zip && \
    mkdir -p \
        /usr/share/texlive/texmf-dist/tex/latex/fontawesome \
        /usr/share/texlive/texmf-dist/fonts/opentype/public/fontawesome \
        /usr/share/texlive/texmf-dist/fonts/tfm/public/fontawesome \
        /usr/share/texlive/texmf-dist/fonts/type1/public/fontawesome \
        /usr/share/texlive/texmf-dist/fonts/map/dvips/fontawesome \
        /usr/share/texlive/texmf-dist/fonts/enc/dvips/fontawesome \
        /usr/share/texlive/texmf-dist/doc/latex/fontawesome && \
    cp fontawesome/tex/*.sty fontawesome/tex/*.fd fontawesome/tex/*.tex \
        /usr/share/texlive/texmf-dist/tex/latex/fontawesome/ && \
    cp fontawesome/opentype/*.otf \
        /usr/share/texlive/texmf-dist/fonts/opentype/public/fontawesome/ && \
    cp fontawesome/tfm/*.tfm \
        /usr/share/texlive/texmf-dist/fonts/tfm/public/fontawesome/ && \
    cp fontawesome/type1/*.pfb \
        /usr/share/texlive/texmf-dist/fonts/type1/public/fontawesome/ && \
    cp fontawesome/map/*.map \
        /usr/share/texlive/texmf-dist/fonts/map/dvips/fontawesome/ && \
    cp fontawesome/enc/*.enc \
        /usr/share/texlive/texmf-dist/fonts/enc/dvips/fontawesome/ && \
    cp fontawesome/doc/* \
        /usr/share/texlive/texmf-dist/doc/latex/fontawesome/ && \
    rm -rf /tmp/fontawesome /tmp/fontawesome.zip

# ---- sourcesanspro (CTAN sourcesans package — ships the sourcesanspro.sty
#      compat shim + Source Sans 3 OpenType fonts). TDS-ready zip. ----
RUN curl -fsSL --retry 3 --connect-timeout 15 --max-time 180 \
        -o /tmp/sourcesans.tds.zip \
        "https://mirrors.ctan.org/install/fonts/sourcesans.tds.zip" && \
    unzip -q /tmp/sourcesans.tds.zip -d /usr/share/texlive/texmf-dist && \
    rm /tmp/sourcesans.tds.zip

# Refresh the TeX file database so kpsewhich finds the new .sty files.
RUN mktexlsr

# Register the OpenType fonts with fontconfig so XeTeX name-based lookups
# (FontAwesome, SourceSans3) resolve.
RUN mkdir -p /usr/local/share/fonts && \
    cp /usr/share/texlive/texmf-dist/fonts/opentype/public/fontawesome/*.otf \
        /usr/local/share/fonts/ && \
    cp /usr/share/texlive/texmf-dist/fonts/opentype/adobe/sourcesans/*.otf \
        /usr/local/share/fonts/ && \
    fc-cache -f

# ---- graphify CLI (pip package graphifyy) ----
# The [openai] extra is required by the deepseek / ds-chat backends: both route
# through _call_openai_compat which imports `openai` (llm.py). Without it,
# semantic extraction fails with ImportError even when DEEPSEEK_API_KEY is
# forwarded via docker_forward_env.
RUN pip install --no-cache-dir "graphifyy[openai]"
