ARG BASE_REGISTRY
ARG BASE_REPO="arkcase/base"
ARG BASE_TAG="8.7.0"

FROM "${BASE_REGISTRY}/${BASE_REPO}:${BASE_TAG}"

#
# Basic Parameters
#
ARG ARCH="amd64"
ARG OS="linux"
ARG VER="1.0.4"
ARG PKG="nettest"
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
        --enable powertools && \
    yum -y install \
        bind-utils \
        jq \
        kubectl \
        nc \
        net-tools \
        nmap \
        openldap-clients \
        openssl \
        python39 \
        python39-pyyaml \
        tcpdump \
        telnet \
        vim \
        wget && \
    update-alternatives --set python /usr/bin/python3.9 && \
    yum -y clean all

COPY nettest.yaml /
COPY wait-for-ports /

#
# Final parameters
#
WORKDIR     /
USER        "${UID}"
ENTRYPOINT  [ "/bin/bash" ]
