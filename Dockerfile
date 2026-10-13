# The image that the experiments run in. The paths below are those of an amd64 image.
FROM ubuntu:22.04

RUN apt-get update && \
    apt-get install -y \
        software-properties-common \
        openjdk-11-jdk \
        git \
        python3 python3-dev python3-pip python3-venv \
        sudo vim wget && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install numpy==2.2.6 scipy==1.15.3 scikit-learn==1.7.2 pandas==2.3.3 matplotlib==3.10.9 tabulate==0.10.0 lxml==6.1.3

# The projects are built and tested with Java 11, and the JFR parser runs with it
ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64/

# The user that the experiments run as
RUN useradd -ms /bin/bash -c "runtimeprofiler" runtimeprofiler && echo "runtimeprofiler:docker" | chpasswd && adduser runtimeprofiler sudo
USER runtimeprofiler
WORKDIR /home/runtimeprofiler/

# Install Maven 3.8.3 in the user's home directory
RUN wget https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.8.3/apache-maven-3.8.3-bin.tar.gz && \
    tar -xzf apache-maven-3.8.3-bin.tar.gz && \
    mv apache-maven-3.8.3/ apache-maven/ && \
    rm apache-maven-3.8.3-bin.tar.gz && \
    echo 'export JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64/' >> ~/.bashrc && \
    echo 'export M2_HOME=${HOME}/apache-maven' >> ~/.bashrc && \
    echo 'export MAVEN_HOME=${HOME}/apache-maven' >> ~/.bashrc && \
    echo 'export PATH=${M2_HOME}/bin:${PATH}' >> ~/.bashrc && \
    echo "alias python=python3" >> ~/.bashrc

# run_experiment.sh starts as root and switches to the user itself
USER root
