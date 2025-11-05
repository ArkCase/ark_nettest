ARG PUBLIC_REGISTRY="public.ecr.aws"
ARG ARCH="amd64"
ARG OS="linux"
ARG VER="1.2.11"
ARG AWS_SRC="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
ARG HELM_SRC="https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3"

ARG BASE_REGISTRY="${PUBLIC_REGISTRY}"
ARG BASE_REPO="arkcase/base"
ARG BASE_VER="8"
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
COPY kubernetes.repo /etc/yum.repos.d/
RUN yum -y install epel-release && \
    yum -y install yum-utils which && \
    yum-config-manager \
        --enable devel \
        --enable powertools \
    && \
    yum -y install \
        bind-utils \
        jq \
        groff \
        kubectl \
        less \
        nc \
        net-tools \
        nmap \
        openldap-clients \
        openssh-clients \
        openssl \
        python39 \
        python39-pyyaml \
        python39-requests \
        screen \
        tcpdump \
        telnet \
        vim \
        wget \
    && \
    update-alternatives --set python3 /usr/bin/python3.9 && \
    yum -y clean all

#
# Add the extra tools
#

# AWS CLI
ENV AWS_CA_BUNDLE="/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
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

COPY nettest.yaml /
COPY --chown=root:root only-once wait-for-ports wait-for-dependencies run-from-env /usr/local/bin/

#
# Final parameters
#
WORKDIR     /
USER        "${UID}"
ENTRYPOINT  [ "/bin/bash" ]
