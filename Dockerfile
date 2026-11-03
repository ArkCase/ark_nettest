ARG FIPS=""
ARG PUBLIC_REGISTRY="public.ecr.aws"
ARG PRIVATE_REGISTRY
ARG ARCH="amd64"
ARG OS="linux"
ARG VER="2.0.0"
ARG K8S_VER="1.34"

ARG BASE_REGISTRY="${PUBLIC_REGISTRY}"
ARG BASE_REPO="arkcase/base"
ARG BASE_VER="24.04"
ARG BASE_VER_PFX=""
ARG BASE_IMG="${BASE_REGISTRY}/${BASE_REPO}${FIPS}:${BASE_VER_PFX}${BASE_VER}"

ARG HELM_REG="${PRIVATE_REGISTRY}"
ARG HELM_REPO="arkcase/rebuild-helm"
ARG HELM_VER="3"
ARG HELM_IMG="${HELM_REG}/${HELM_REPO}${FIPS}:${BASE_VER_PFX}${HELM_VER}"

FROM "${HELM_IMG}" AS helm-src

ARG BASE_IMG

FROM "${BASE_IMG}"

#
# Basic Parameters
#
ARG ARCH
ARG OS
ARG VER
ARG AWS_SRC="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
ARG K8S_VER
ARG APP_UID="0"

#
# Some important labels
#
LABEL ORG="Armedia LLC"
LABEL MAINTAINER="Armedia Devops Team <devops@armedia.com>"
LABEL APP="Network Tester"
LABEL VERSION="${VER}"
LABEL IMAGE_SOURCE="https://github.com/ArkCase/ark_nettest"

ENV AWS_CA_BUNDLE="${CA_TRUSTS_PEM}"

#
# Full update
#
RUN --mount=type=bind,target=/src \
    --mount=type=bind,from=helm-src,target=/helm \
    export K8S_KEY="/etc/apt/trusted.gpg.d/kubernetes.gpg" && \
    export K8S_LIST="/etc/apt/sources.list.d/kubernetes.list" && \
    curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_VER}/deb/Release.key" | \
        gpg --dearmor -o "${K8S_KEY}" && \
    chmod 644 "${K8S_KEY}" && \
    echo "deb [signed-by=${K8S_KEY}] https://pkgs.k8s.io/core:/stable:/v${K8S_VER}/deb/ /" | \
    tee "${K8S_LIST}" && \
    chmod 644 "${K8S_LIST}" && \
    apt-get update && \
    apt-get -y install \
        kubectl \
        ldap-utils \
        less \
        netcat-openbsd \
        net-tools \
        nmap \
        openssh-client \
        python3-requests \
        screen \
        tcpdump \
        vim \
      && \
    apt-get clean && \
    kubectl completion bash > /usr/share/bash-completion/completions/kubectl && \
    mkdir -p "/aws" && \
    curl "${AWS_SRC}" -o "/aws/awscliv2.zip" && \
    cd "/aws" && \
    unzip "awscliv2.zip" && \
    ./aws/install && \
    cd / && \
    rm -rf "/aws" && \
    echo "complete -C '/usr/local/bin/aws_completer' aws" > /etc/profile.d/02-aws.sh && \
    install -o root -g root -m 0755 /helm/helm /usr/local/bin && \
    helm completion bash > /usr/share/bash-completion/completions/helm && \
    install -o root -g root -m 0444 /src/nettest-security.yaml / && \
    install -o root -g root -m 0755 /src/entrypoint /usr/local/bin && \
    ( tar --owner=root --group=root --mode=u=rwX,go=rX -cf - /src/scripts ) | tar --strip-components=2 -C "/usr/local/bin" -xvf -

#
# Final parameters
#
WORKDIR     /
USER        "${APP_UID}"
