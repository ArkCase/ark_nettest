FROM rockylinux:8

#
# Basic Parameters
#
ARG ARCH="amd64"
ARG OS="linux"
ARG VER="1.0.2"
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
        telnet \
        vim \
        wget && \
    yum -y clean all

#
# Final parameters
#
WORKDIR     /
USER        "${UID}"
ENTRYPOINT  [ "sleep", "infinity" ]
