FROM rockylinux:8

#
# Basic Parameters
#
ARG ARCH="amd64"
ARG OS="linux"
ARG VER="1.0.0"
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
RUN yum -y install epel-release && \
    yum -y update && \
    yum -y install yum-utils which && \
    yum-config-manager \
        --enable devel \
        --enable powertools

RUN yum -y install \
        bind-utils \
        jq \
        nc \
        net-tools \
        nmap \
        telnet \
        wget

#
# Final parameters
#
WORKDIR     /
ENTRYPOINT  [ "/bin/bash" ]
