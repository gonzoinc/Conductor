{# Apply on the MySQL/MariaDB minion before conductor/init.sls on service nodes. #}
{% set name = salt['pillar.get']('conductor:mysql:database', '') %}
{% set login = salt['pillar.get']('conductor:mysql:user', '') %}
{% set allowed_host = salt['pillar.get']('conductor:mysql:allowed_host', 'localhost') %}
{% set auth_plugin = salt['pillar.get']('conductor:mysql:auth_plugin', '') %}
{% set password = salt['pillar.get']('conductor:mysql_password', '') %}
{% set create_database = salt['pillar.get']('conductor:mysql:create_database', False) %}
{% if not name or not login or not password %}
conductor-mysql-pillar-required:
  test.fail_without_changes:
    - name: Set conductor:mysql database/user and conductor:mysql_password in private pillar.
{% else %}
{# Salt's MySQL execution module must have a MySQL Python driver in its own runtime. #}
{# Configure privileged database access through the minion's private Salt settings. #}
{% if create_database %}
conductor-mysql-database:
  mysql_database.present:
    - name: {{ name }}
    - character_set: utf8mb4
    - collate: utf8mb4_bin
{% endif %}

conductor-mysql-user:
  mysql_user.present:
    - name: {{ login }}
    - host: {{ allowed_host }}
    - password: {{ password | yaml_dquote }}
{% if auth_plugin %}
    - auth_plugin: {{ auth_plugin }}
{% endif %}
{% if create_database %}
    - require:
      - mysql_database: conductor-mysql-database
{% endif %}

conductor-mysql-grants:
  mysql_grants.present:
    - grant: SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,INDEX
    - database: {{ name }}.*
    - user: {{ login }}
    - host: {{ allowed_host }}
    - require:
      - mysql_user: conductor-mysql-user
{% endif %}
