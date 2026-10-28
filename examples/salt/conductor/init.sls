{# Apply on each Conductor service node. Bundle/config sources are reviewed Salt files. #}
{% set bundle_sha256 = salt['pillar.get']('conductor:bundle_sha256') %}
{% set mysql_enabled = salt['pillar.get']('conductor:mysql_enabled', False) %}
{% set mysql_tcp = salt['pillar.get']('conductor:mysql_tcp', False) %}
{% if not bundle_sha256 or bundle_sha256|length != 64 %}
conductor-bundle-sha-required:
  test.fail_without_changes:
    - name: Set conductor:bundle_sha256 to the reviewed release tarball SHA256.
{% else %}
conductor-runtime-packages:
  pkg.installed:
    - pkgs:
      - python3
      - python3-venv
      - python3-yaml

conductor:
  group.present:
    - system: True
  user.present:
    - system: True
    - gid: conductor
    - home: /var/lib/conductor
    - createhome: False
    - shell: /usr/sbin/nologin
    - require:
      - group: conductor

/etc/conductor:
  file.directory:
    - user: root
    - group: conductor
    - mode: '0750'
    - require:
      - user: conductor

/etc/conductor/conductor.yaml:
  file.managed:
    - source: salt://conductor/conductor.yaml
    - user: root
    - group: conductor
    - mode: '0640'
    - require:
      - file: /etc/conductor

{% if mysql_enabled %}
/etc/conductor/mysql-password:
  file.managed:
    - contents_pillar: conductor:mysql_password
    - user: root
    - group: conductor
    - mode: '0640'
    - require:
      - file: /etc/conductor

{% if mysql_tcp %}
/etc/conductor/mysql-ca.pem:
  file.managed:
    - source: salt://conductor/mysql-ca.pem
    - user: root
    - group: conductor
    - mode: '0640'
    - require:
      - file: /etc/conductor
{% endif %}
{% endif %}

/usr/local/lib/conductor:
  file.directory:
    - user: root
    - group: root
    - mode: '0755'

/usr/local/lib/conductor/scripts:
  file.directory:
    - user: root
    - group: root
    - mode: '0755'
    - require:
      - file: /usr/local/lib/conductor

/usr/local/lib/conductor/scripts/install_host.py:
  file.managed:
    - source: salt://conductor/install_host.py
    - user: root
    - group: root
    - mode: '0644'
    - require:
      - file: /usr/local/lib/conductor/scripts

/usr/local/lib/conductor/install-host.sh:
  file.managed:
    - source: salt://conductor/install-host.sh
    - user: root
    - group: root
    - mode: '0755'

/var/cache/conductor:
  file.directory:
    - user: root
    - group: root
    - mode: '0700'

/var/cache/conductor/conductor-source.tar.gz:
  file.managed:
    - source: salt://conductor/conductor-source.tar.gz
    - source_hash: sha256={{ bundle_sha256 }}
    - user: root
    - group: root
    - mode: '0600'
    - require:
      - file: /var/cache/conductor

conductor-host-installed:
  cmd.run:
    - name: /usr/local/lib/conductor/install-host.sh --config /etc/conductor/conductor.yaml --bundle /var/cache/conductor/conductor-source.tar.gz --sha256 {{ bundle_sha256 }}
    - unless: /usr/local/lib/conductor/install-host.sh --check --config /etc/conductor/conductor.yaml --bundle /var/cache/conductor/conductor-source.tar.gz --sha256 {{ bundle_sha256 }}
    - require:
      - pkg: conductor-runtime-packages
      - file: /etc/conductor/conductor.yaml
      - file: /usr/local/lib/conductor/scripts/install_host.py
      - file: /usr/local/lib/conductor/install-host.sh
      - file: /var/cache/conductor/conductor-source.tar.gz
{% if mysql_enabled %}
      - file: /etc/conductor/mysql-password
{% if mysql_tcp %}
      - file: /etc/conductor/mysql-ca.pem
{% endif %}
{% endif %}

conductor-running:
  service.running:
    - name: conductor.service
    - enable: True
    - require:
      - cmd: conductor-host-installed
{% if mysql_enabled %}
    - watch:
      - file: /etc/conductor/mysql-password
{% if mysql_tcp %}
      - file: /etc/conductor/mysql-ca.pem
{% endif %}
{% endif %}
{% endif %}
