ARG PUBLIC_REGISTRY="public.ecr.aws"
ARG BASE_REPO="arkcase/base"
ARG BASE_TAG="8.7.0"
ARG ARCH="amd64"
ARG OS="linux"
ARG VER="1.0.5"
ARG AWS_SRC="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"

FROM "${PUBLIC_REGISTRY}/${BASE_REPO}:${BASE_TAG}"

#
# Basic Parameters
#
ARG ARCH
ARG OS
ARG VER
ARG AWS_SRC
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
    yum -y update && \
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
        openssl \
        python39 \
        python39-pyyaml \
        python39-requests \
        tcpdump \
        telnet \
        vim \
        wget \
    && \
    update-alternatives --set python /usr/bin/python3.9 && \
    yum -y clean all && \
    mkdir -p "/aws" && \
    curl "${AWS_SRC}" -o "/aws/awscliv2.zip" && \
    cd "/aws" && \
    unzip "awscliv2.zip" && \
    ./aws/install && \
    cd / && \
    rm -rf "/aws"

COPY nettest.yaml /
COPY wait-for-ports /

#
# Final parameters
#
WORKDIR     /
USER        "${UID}"
ENTRYPOINT  [ "/bin/bash" ]
