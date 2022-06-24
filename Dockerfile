FROM rockylinux:8

#
# Basic Parameters
#
ARG ARCH="amd64"
ARG OS="linux"
ARG VER="1.0.1"
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
        openldap-clients \
        telnet \
        wget

COPY entrypoint /

#
# Final parameters
#
WORKDIR     /
ENTRYPOINT  [ "/entrypoint" ]
