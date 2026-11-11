FROM python:3.12-slim

# graphviz binary is needed to render ER diagrams
RUN apt-get update \
 && apt-get install -y --no-install-recommends graphviz \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN useradd -m -u 1000 student && chmod 755 /home/student   # Debian makes home 0700; let other uids (CI, Podman) traverse it
USER student
WORKDIR /home/student/playground
ENV PYTHONPATH=/home/student/playground

EXPOSE 8888
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", \
     "--ServerApp.token=", "--ServerApp.password=", \
     "--ServerApp.root_dir=/home/student/playground"]
