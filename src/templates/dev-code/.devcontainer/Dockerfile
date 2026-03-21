FROM mcr.microsoft.com/devcontainers/base:noble

# ===== Env =====
ARG USERNAME=vscode \
    FZF_VERSION=0.68.0

ENV USERNAME=${USERNAME} \
    USER_HOME=/home/${USERNAME} \
    HISTFILE=/commandhistory/.history \
    HISTSIZE=10000 \
    EDITOR=vim \
    VISUAL=vim
ENV SHELL=/bin/zsh

# ===== Install deps + fzf =====
RUN apt-get update && apt-get install -y --no-install-recommends \
        fzf \
        rsync \
        vim \
        gh \
    && rm -rf /var/lib/apt/lists/* \
    && ARCH="$(uname -m)" \
    && [ "$ARCH" = "x86_64" ] && ARCH=amd64 || ARCH=arm64 \
    && curl -fsSL "https://github.com/junegunn/fzf/releases/download/v${FZF_VERSION}/fzf-${FZF_VERSION}-linux_${ARCH}.tar.gz" \
        | tar -xz -C /usr/local/bin

# ===== Setup history =====
RUN mkdir -p /commandhistory \
    && touch /commandhistory/.history \
    && chown -R ${USERNAME}:${USERNAME} /commandhistory

# ===== Install Powerlevel10k =====
RUN git clone --depth=1 https://github.com/romkatv/powerlevel10k.git \
    ${USER_HOME}/.oh-my-zsh/custom/themes/powerlevel10k \
    && chown -R ${USERNAME}:${USERNAME} ${USER_HOME}/.oh-my-zsh

# ===== Setup zsh config =====

RUN ZSHRC="${USER_HOME}/.zshrc" \
    && sed -i "s/ZSH_THEME=\".*/ZSH_THEME=\"powerlevel10k\/powerlevel10k\"/g" "$ZSHRC" \
    && cat <<EOF >> "$ZSHRC"
source <(fzf --zsh)
POWERLEVEL9K_DISABLE_CONFIGURATION_WIZARD=true
POWERLEVEL9K_SHORTEN_STRATEGY="truncate_to_last"
POWERLEVEL9K_LEFT_PROMPT_ELEMENTS=(user dir vcs status)
POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=()
POWERLEVEL9K_STATUS_OK=false
POWERLEVEL9K_STATUS_CROSS=true
POWERLEVEL9K_PROMPT_ON_NEWLINE=true
EOF

# ===== Fix USER HOME permissions =====
RUN chown -R ${USERNAME}:${USERNAME} ${USER_HOME}

# ===== Switch user =====
USER ${USERNAME}