ARG PUBLIC_REGISTRY="public.ecr.aws"
ARG ARCH="amd64"
ARG OS="linux"
ARG VER="2.0.0"
ARG AWS_SRC="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
ARG HELM_SRC="https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3"
ARG K8S_VER="1.34"

ARG BASE_REGISTRY="${PUBLIC_REGISTRY}"
ARG BASE_REPO="arkcase/base"
ARG BASE_VER="22.04"
ARG BASE_VER_PFX=""
ARG BASE_IMG="${BASE_REGISTRY}/${BASE_REPO}:${BASE_VER_PFX}${BASE_VER}"

FROM "${BASE_IMG}"

#
# Basic Parameters
#
ARG ARCH
ARG OS
ARG VER
ARG AWS_SRC
ARG HELM_SRC
ARG HELM_SH="/helm.sh"
ARG K8S_VER
ARG UID="0"

#
# Some important labels
#
LABEL ORG="Armedia LLC"
LABEL MAINTAINER="Armedia Devops Team <devops@armedia.com>"
LABEL APP="Network Tester"
LABEL VERSION="${VER}"
LABEL IMAGE_SOURCE="https://github.com/ArkCase/ark_nettest"

#
# Full update
#
RUN export K8S_KEY="/etc/apt/trusted.gpg.d/kubernetes.gpg" && \
    export K8S_LIST="/etc/apt/sources.list.d/kubernetes.list" && \
    curl -fsSL "https://pkgs.k8s.io/core:/stable:/v${K8S_VER}/deb/Release.key" | \
        gpg --dearmor -o "${K8S_KEY}" && \
    chmod 644 "${K8S_KEY}" && \
    echo "deb [signed-by=${K8S_KEY}] https://pkgs.k8s.io/core:/stable:/v${K8S_VER}/deb/ /" | \
    tee "${K8S_LIST}" && \
    chmod 644 "${K8S_LIST}"

RUN apt-get update && \
    apt-get -y install \
        kubectl \
        ldap-utils \
        less \
        netcat \
        net-tools \
        nmap \
        openssh-client \
        python3-requests \
        screen \
        tcpdump \
        telnet \
        vim \
      && \
    apt-get clean

#
# Add the extra tools
#

# AWS CLI
RUN mkdir -p "/aws" && \
    curl "${AWS_SRC}" -o "/aws/awscliv2.zip" && \
    cd "/aws" && \
    unzip "awscliv2.zip" && \
    ./aws/install && \
    cd / && \
    rm -rf "/aws"

# Helm
RUN curl -fsSL -o "${HELM_SH}" "${HELM_SRC}" && \
    bash "${HELM_SH}" && \
    rm -rf "${HELM_SH}"

COPY nettest-security.yaml /
COPY --chown=root:root --chmod=0755 entrypoint /
COPY --chown=root:root --chmod=0755 scripts/* /usr/local/bin/

#
# Final parameters
#
WORKDIR     /
USER        "${UID}"
ENTRYPOINT  [ "/entrypoint" ]
