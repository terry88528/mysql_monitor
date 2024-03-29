# -*- coding: utf-8 -*-

# yum install openssl-devel python-devel libffi-devel -y
# pip install flask flask-login gevent threadpool pymysql DBUtils paramiko

import json
import os
import gzip
import StringIO
import base64
import sys

from gevent import pywsgi
from flask import Flask, render_template, request, app, redirect, url_for
from flask_login import login_user, login_required, logout_user, LoginManager, current_user

import settings
import backup
from monitor.entitys import BaseClass
from monitor import user_login, new_slow_log, report
from monitor import cache, server, mysql_manager, tablespace, chart, common, binlog_util


# region load data on run

reload(sys)
sys.setdefaultencoding("UTF-8")

app = Flask("mysql_web")
app.secret_key = os.urandom(24)
app.config["SECRET_KEY"] = os.urandom(24)
login_manager = LoginManager()
login_manager.session_protection = "strong"
login_manager.login_view = "login_home"
login_manager.init_app(app=app)

mysql_cache = cache.Cache()
mysql_cache.load_all_host_infos()
monitor_server = server.MonitorServer()
monitor_server.load()
monitor_server.start()
#monitor_server.invoke_check_tablespace_method()


# endregion

# region mysql api

@app.route("/mysql", methods=['GET', 'POST'])
@login_required
def get_mysql_data():
    return gzip_compress(render_template("mysqls.html", mysql_infos=mysql_cache.get_all_host_infos(keys=json.loads(request.values["keys"]))))


@app.route("/mysql/<int:id>")
@login_required
def get_mysql_data_by_id(id):
    return get_monitor_data(data_host=convert_object_to_list(mysql_cache.get_linux_info(id)),
                            data_status=convert_object_to_list(mysql_cache.get_status_info(id)),
                            data_repl=convert_object_to_list(mysql_cache.get_repl_info(id)),
                            data_innodb=convert_object_to_list(mysql_cache.get_innodb_info(id)),
                            data_engine_innodb=mysql_cache.get_engine_innodb_status_infos(id))


@app.route("/mysql/processlist/<int:host_id>", methods=['GET', 'POST'])
@login_required
def get_mysql_processlist(host_id):
    return render_template("show_processlist.html", processlist_infos=mysql_manager.get_show_processlist_infos(host_id), host_info=cache.Cache().get_host_info(host_id))


@app.route("/mysql/kill/<int:host_id>/<int:thread_id>", methods=['GET', 'POST'])
@login_required
def kill_mysql_thread(host_id, thread_id):
    return mysql_manager.kill_mysql_thread(host_id, thread_id)


# endregion

# region mysql status api

@app.route("/status", methods=['GET', 'POST'])
@login_required
def get_status_data():
    # print(current_user.id, current_user.username)
    return gzip_compress(get_monitor_data(data_status=mysql_cache.get_all_status_infos(keys=json.loads(request.values["keys"]))))


@app.route("/status/<int:id>")
@login_required
def get_status_data_by_id(id):
    return get_monitor_data(data_status=convert_object_to_list(mysql_cache.get_status_infos(id)))


# endregion

# region innodb api

@app.route("/innodb", methods=['GET', 'POST'])
@login_required
def get_innodb_data():
    return gzip_compress(get_monitor_data(data_innodb=mysql_cache.get_all_innodb_infos(keys=json.loads(request.values["keys"]))))


@app.route("/innodb/<int:id>")
@login_required
def get_innodb_data_by_id(id):
    return get_monitor_data(data_innodb=convert_object_to_list(mysql_cache.get_innodb_info(id)),
                            data_engine_innodb=mysql_cache.get_engine_innodb_status_infos(id))


# endregion

# region replication api

@app.route("/replication", methods=['GET', 'POST'])
@login_required
def get_replication_data():
    return gzip_compress(get_monitor_data(data_repl=mysql_cache.get_all_repl_infos(keys=json.loads(request.values["keys"]))))


@app.route("/replication/<int:id>")
@login_required
def get_replication_data_by_id(id):
    return get_monitor_data(slave_status=mysql_manager.get_show_slave_status(id))


# endregion

# region tablespace api

@app.route("/tablespace")
@login_required
def get_tablespace():
    return render_template("tablespace.html", host_infos=mysql_cache.get_all_host_infos())


@app.route("/tablespace/check/invoke")
def execute_check_tablespace():
    monitor_server.invoke_check_tablespace_method()
    return "invoke ok, please wait."


@app.route("/tablespace/sort/", methods=["POST"])
@login_required
def sort_tablespace():
    json_obj = get_object_from_json(request.form)
    if (json_obj.host_id <= 0):
        return get_table_html(tablespace_list=tablespace.sort_tablespace(json_obj.sort_type_id))
    else:
        return get_table_html(page_number=json_obj.page_number,
                              page_list=get_page_number_list(json_obj.page_number),
                              table_list=tablespace.sort_tablespace_by_host_id(json_obj.host_id, json_obj.sort_type_id, json_obj.page_number, json_obj.table_name))


@app.route("/tablespace/report")
@login_required
def send_tablespace_report_mail():
    report.send_report_everyday()
    return "send ok"


@app.route("/tablespace/table/detail", methods=["POST"])
@login_required
def get_table_detail():
    return get_table_html(table_detail=tablespace.get_table_info(int(request.form["host_id"]), request.form["table_schema"], request.form["table_name"]))


def get_table_html(tablespace_list=None, table_list=None, table_detail=None, page_number=1, page_list=None):
    return render_template("tablespace_dispaly.html", host_tablespace_infos=tablespace_list, tablespace_status=table_list, table_detail_info=table_detail, page_number=page_number, page_list=page_list)


@app.route("/tablespace/search", methods=["POST"])
@login_required
def search_table():
    return get_table_html(table_list=tablespace.search_table(int(request.form["host_id"]), request.form["table_name"]))


@app.route("/index/duplicate/<int:host_id>", methods=["POST"])
@login_required
def pt_duplicate_key_checker(host_id):
    return tablespace.pt_duplicate_key_checker(host_id)

# endregion

# region common method

def gzip_compress(content):
    zbuf = StringIO.StringIO()
    zfile = gzip.GzipFile(mode='wb', compresslevel=9, fileobj=zbuf)
    zfile.write(content)
    zfile.close()
    return base64.b64encode(zbuf.getvalue())


def gzip_decompress(content):
    compresseddata = base64.b64decode(content)
    compressedstream = StringIO.StringIO(compresseddata)
    gzipper = gzip.GzipFile(fileobj=compressedstream)
    data = gzipper.read()
    return data


def convert_object_to_list(obj):
    list_tmp = None
    if (obj != None):
        list_tmp = [obj]
    return list_tmp


def get_monitor_data(data_status=None, data_innodb=None, data_repl=None, data_engine_innodb=None, data_host=None, slave_status=None, tablespace_status=None):
    return render_template("monitor.html",
                           data_engine_innodb=data_engine_innodb,
                           data_status=data_status,
                           data_innodb=data_innodb,
                           data_repl=data_repl,
                           data_host=data_host,
                           slave_status=slave_status,
                           tablespace_status=tablespace_status)


# endregion

# region slow log api

@app.route("/slowlog")
@login_required
def slow_log_home():
    return render_template("new_slow_log_home.html", host_infos=mysql_cache.get_all_host_infos(), user_infos=mysql_cache.get_mysql_web_user_infos())


@app.route("/newslowlog/", methods=['POST'])
@login_required
def new_get_slow_logs():
    page_number = 1
    if (request.form.to_dict().has_key("page_number")):
        page_number = int(request.form["page_number"])
    return render_template("slow_log_display.html",
                           slow_logs=new_slow_log.get_slow_logs(request.form["host_ids"],
                                                                start_datetime=request.form["start_datetime"],
                                                                stop_datetime=request.form["stop_datetime"],
                                                                order_by_type=int(request.form["order_by_options"]),
                                                                page_number=page_number,
                                                                status=int(request.form["slow_log_status"])),
                           slow_log_infos=None,
                           page_number=page_number,
                           page_list=get_page_number_list(page_number))


@app.route("/newslowlog/detail/<int:checksum>/<int:host_id>")
@login_required
def new_get_slow_log_detail(checksum, host_id):
    return render_template("new_slow_log_detail.html", slow_low_info=new_slow_log.get_slow_log_detail(checksum, host_id))
    # return render_template("slow_log_detail.html", slow_low_info=new_slow_log.get_slow_log_detail(checksum, host_id))


@app.route("/newslowlog/explain/<int:checksum>/<int:host_id>")
def get_explain_infos(checksum, host_id):
    return render_template("slow_log_detail.html", slow_low_info=new_slow_log.get_slow_log_detail(checksum, host_id))


@app.route("/newslowlog/review/detail/<int:checksum>")
def get_review_detail(checksum):
    return new_slow_log.get_review_detail_by_checksum(checksum)


@app.route("/newslowlog/review/update", methods=['POST'])
def update_review_detail():
    obj = get_object_from_json(request.form)
    obj.user_id = current_user.id
    return new_slow_log.update_review_detail(obj)


def get_page_number_list(page_number):
    if (page_number <= 5):
        page_list = range(1, 10)
    else:
        page_list = range(page_number - 5, page_number + 6)
    return page_list


# endregion

# region other api

@app.route("/os", methods=['GET', 'POST'])
@login_required
def get_os_infos():
    return gzip_compress(get_monitor_data(data_host=mysql_cache.get_all_linux_infos(keys=json.loads(request.values["keys"]))))


@app.route("/home", methods=['GET', 'POST'])
@login_required
def home():
    return render_template("home.html", host_infos=mysql_cache.get_all_host_infos(), username=current_user.username, interval=settings.UPDATE_INTERVAL * 1000)


@app.route("/home/binlog")
@login_required
def get_test():
    return render_template("binlog.html")


@app.route("/home/load")
def load_all_host_infos():
    mysql_cache.load_all_host_infos()
    return "load ok"


# endregion

# region user web api

@app.route("/user")
@login_required
def user_home():
    return render_template("user.html", host_infos=mysql_cache.get_all_host_infos())


@app.route("/user/query", methods=['GET', 'POST'])
@login_required
def select_user():
    print(request.form)
    host_id = int(request.form["server_id"])
    return render_template("user_display.html", host_id=host_id, user_infos=user.MySQLUser(host_id).query_user(request.form["user_name"], request.form["ip"]))


@app.route("/user/db")
@login_required
def get_all_database_name():
    return user.MySQLUser(1).get_all_database_name()


@app.route("/user/<name>/<ip>")
@login_required
def get_detail_priv_by_user(name, ip):
    return user.MySQLUser(1).get_privs_by_user(name, ip)


@app.route("/user/detail/<int:host_id>/<name>/<ip>")
@login_required
def get_user_detail(host_id, name, ip):
    return user.MySQLUser(host_id).get_privs_by_user(name, ip)


@app.route("/user/add")
def add_user():
    pass


@app.route("/user/drop/<int:host_id>/<name>/<ip>")
@login_required
def drop_user(host_id, name, ip):
    return user.MySQLUser(host_id).drop_user(name, ip)


# endregion

# region login api

@app.route("/login/verfiy", methods=['GET', 'POST'])
def login_verfiy():
    result = BaseClass(None)
    result.error = ""
    result.success = ""
    user_tmp = user_login.User(request.form["userName"])
    if (user_tmp.verify_password(request.form["passWord"], result) == True):
        login_user(user_tmp)
    return json.dumps(result, default=lambda o: o.__dict__)


@app.route("/logout", methods=['GET', 'POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for("login_home"))


@login_manager.user_loader
def load_user(user_id):
    return user_login.User(None).get(user_id)


@app.route("/login")
def login_home():
    return "<p hidden>login_error</p>" + render_template("login.html")


# endregion

# region chart api

@app.route("/chart")
@login_required
def chart_home():
    return render_template("chart.html", host_infos=mysql_cache.get_all_host_infos(), interval=settings.UPDATE_INTERVAL * 1000)


@app.route("/chart/<int:host_id>", methods=["GET", "POST"])
@login_required
def get_chart_data_by_host_id(host_id):
    return chart.get_chart_data_by_host_id(host_id)


@app.route("/chart_new")
@login_required
def chart_home_new():
    return render_template("chart_new.html", host_infos=mysql_cache.get_all_host_infos(), chart_options=chart.chart_options)


@app.route("/chart_new/get_data/", methods=["POST"])
@login_required
def get_chart_data_by_attribute_names():
    return chart.get_chart_data(get_object_from_json(request.form))


@app.route("/chart_new/option/<int:key>", methods=["GET", "POST"])
@login_required
def get_chart_options(key):
    return chart.get_chart_option(key)


@app.route("/chart_new/new/<int:host_id>", methods=["GET", "POST"])
@login_required
def open_new_chart_page(host_id):
    return render_template("chart_mysql.html", host_info=cache.Cache().get_host_info(host_id))


@app.route("/chart/home/<int:host_id>", methods=["GET", "POST"])
@login_required
def get_chart_home(host_id):
    return render_template("chart_auto.html", host_info=cache.Cache().get_host_info(host_id))


@app.route("/chart/config", methods=["GET", "POST"])
@login_required
def get_chart_config_infos():
    return chart.get_chart_config_infos()


@app.route("/chart/data/<int:host_id>", methods=["GET", "POST"])
@login_required
def get_chart_data(host_id):
    return chart.get_chart_data_by_config(host_id)


# endregion

# region config

@app.route("/config")
@login_required
def get_config_html():
    return render_template("config.html", config_info=get_config_options_value())


@app.route("/config/update", methods=["POST"])
@login_required
def update_config_options():
    obj = get_object_from_json(request.form)
    if (int(obj.update_type) == 1):
        settings.UPDATE_INTERVAL = int(obj.status_refresh)
        settings.LINUX_UPDATE_INTERVAL = int(obj.linux_os_refresh)
        settings.INNODB_UPDATE_INTERVAL = int(obj.innodb_engine_refresh)
    if (int(obj.update_type) == 2):
        settings.EMAIL_HOST = obj.email_host
        settings.EMAIL_PORT = obj.email_port
        settings.EMAIL_USER = obj.email_user
        settings.EMAIL_PASSWORD = obj.email_password
        settings.EMAIL_SEND_USERS = obj.email_send_users
    return "save success."


def get_config_options_value():
    info = BaseClass(None)
    info.host = settings.EMAIL_HOST
    info.port = settings.EMAIL_PORT
    info.user = settings.EMAIL_USER
    info.password = settings.EMAIL_PASSWORD
    info.send_users = settings.EMAIL_SEND_USERS
    info.status_refresh = settings.UPDATE_INTERVAL
    info.linux_os_refresh = settings.LINUX_UPDATE_INTERVAL
    info.innodb_engine_refresh = settings.INNODB_UPDATE_INTERVAL
    return info


def get_object_from_json(json_value):
    obj = BaseClass(None)
    for key, value in dict(json_value).items():
        if (value[0].isdigit()):
            setattr(obj, key, int(value[0]))
        else:
            setattr(obj, key, value[0])
    return obj


def get_object_from_json_tmp(json_value):
    obj = BaseClass(None)
    for key, value in json.loads(json_value).items():
        if (len(str(value)) <= 0):
            setattr(obj, key, value)
        elif (str(value).isdigit()):
            setattr(obj, key, int(value))
        else:
            if (value == "null"):
                setattr(obj, key, None)
            else:
                setattr(obj, key, value)
    obj.current_user_id = current_user.id
    return obj

# endregion

# region backup

@app.route("/backup")
@login_required
def get_backup_html():
    return render_template("backup.html", host_infos=mysql_cache.get_all_host_infos())


@app.route("/backup/add", methods=["POST"])
@login_required
def add_backup_task():
    return backup.add_backup(get_object_from_json(request.form))


# endregion

#region mysql host

@app.route("/host", methods=["GET", "POST"])
@login_required
def get_mysql_host_home():
    return render_template("mysql_host.html")


@app.route("/host/query", methods=["GET", "POST"])
@login_required
def get_mysql_host_infos():
    return render_template("mysql_host_view.html", mysql_host_infos=cache.Cache().get_all_host_infos())


@app.route("/host/add", methods=["GET", "POST"])
@login_required
def add_mysql_host_info():
    return mysql_manager.add_mysql_host_info(get_object_from_json_tmp(request.get_data()))


@app.route("/host/test/ssh", methods=["GET", "POST"])
@login_required
def test_ssh_connection_is_ok():
    if (common.test_ssh_connection_is_ok(get_object_from_json_tmp(request.get_data()))):
        return "ssh connection ok."
    return "ssh connection error."


@app.route("/host/test/mysql", methods=["GET", "POST"])
@login_required
def test_mysql_connection_is_ok():
    if (common.test_mysql_connection_is_ok(get_object_from_json_tmp(request.get_data()))):
        return "mysql connection ok."
    return "mysql connection error."


@app.route("/host/start/<int:host_id>", methods=["GET", "POST"])
@login_required
def start_mysql_host_info(host_id):
    return mysql_manager.start_mysql_host_info(host_id)


@app.route("/host/delete/<int:host_id>", methods=["GET", "POST"])
@login_required
def delete_mysql_host_info(host_id):
    return mysql_manager.delete_mysql_host_info(host_id)

@app.route("/host/query/<int:host_id>", methods=["GET", "POST"])
@login_required
def get_mysql_info_by_host_id(host_id):
    return mysql_manager.get_mysql_info(host_id)

#endregion

#region binlog


@app.route("/binlog", methods=["GET", "POST"])
@login_required
def get_binlog():
    return render_template("binlog.html", host_infos=cache.Cache().get_all_host_infos())


@app.route("/binlog/logs/<int:host_id>", methods=["GET", "POST"])
@login_required
def get_show_master_logs(host_id):
    binlog_list = mysql_manager.get_show_master_logs(host_id)
    html_str = """<select id="binlog_file_name" class="selectpicker show-tick form-control bs-select-hidden">
                      {0}
                  </select>"""
    options_str = ""
    if (len(binlog_list) > 0):
        for info in binlog_list:
            options_str += "<option value=\"{0}\">{1}</option>".format(info.log_name, info.log_name)
    else:
        options_str = "<option value=\"No Binlog\" disable>No Binlog</option>"
    return html_str.format(options_str)


@app.route("/binlog/data/", methods=["GET", "POST"])
@login_required
def get_binlog_data():
    return binlog_util.get_binlog(get_object_from_json_tmp(request.get_data()))


#endregion

#region alarm config

@app.route("/alarm", methods=["GET", "POST"])
@login_required
def alarm_config():
    return render_template("alarm_config.html")


#endregion


if __name__ == '__main__':
    if (settings.LINUX_OS):
        print("linux start ok.")
        # server = pywsgi.WSGIServer(("0.0.0.0", 5000), app)
        # server.serve_forever()
        app.run(debug=False, host="0.0.0.0", port=5000, use_reloader=False, threaded=True)
    if (settings.WINDOWS_OS):
        print("windows start ok.")
        app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=True, threaded=True)
