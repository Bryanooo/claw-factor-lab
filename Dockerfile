FROM python:3.11.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
ARG QLIB_REF=da920b7f954f48ab1bb64117c976710de198373e
RUN pip install --no-cache-dir -c /tmp/requirements.txt "pyqlib @ git+https://github.com/microsoft/qlib.git@${QLIB_REF}"
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir -r /tmp/requirements.lock.txt && pip check
RUN pip freeze > /opt/requirements.resolved.txt
COPY platform-requirements.txt /tmp/platform-requirements.txt
RUN pip install --no-cache-dir -c /tmp/requirements.lock.txt -r /tmp/platform-requirements.txt && pip check
RUN pip freeze > /opt/requirements.resolved.txt
COPY . /workspace
CMD ["uvicorn", "platform_service.api:app", "--host", "0.0.0.0", "--port", "8080"]
