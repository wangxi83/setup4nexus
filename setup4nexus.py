# __author__ = 'wangx'
# -*- coding: UTF-8 -*-
#
# 1、使用setup.py打包wheel
# 2、在dist目录构建依赖的wheel
# 3、将打包后的wheel和依赖的wheel上传到指定的nexus
# 整体支持三个参数: pip源、nexus地址、是否保留打包好的内容
# opts, args = getopt.getopt(sys.argv[1:], "i:t:k", ["source=", "nexus=", "keep-whl="])
#
# 其中(-t， --nexus= )是必选参数
# 默认会保留打包后的内容, (-k, --keep-whl)=True


from pathlib import Path as pathlib
import os, asyncio, sys, getopt, requests
import traceback


def is_win():
    import sys
    return sys.platform == "win32"


"""
把打包好的wheel以及依赖上床到nexus
"""
import subprocess


async def exec_shell(command: []):
    """
    执行指令，并返回(out, err)
    :param command:
    :return:
    """
    print(command)
    # s = subprocess.Popen(command, stderr=subprocess.PIPE, stdout=subprocess.PIPE, shell=not isinstance([], list))
    # s.wait()
    # out = s.stdout.read().decode("GBK")
    # err = s.stderr.read().decode("GBK")
    # s.stdout.close()
    # s.stderr.close()
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=not isinstance([], list)) as process:
        try:
            out, err = await loop.run_in_executor(None, process.communicate)
            try:
                return out.decode("UTF-8"), err.decode("UTF-8")
            except UnicodeDecodeError :
                return out.decode("GBK"), err.decode("GBK")
        except Exception:  # muh pycodestyle
            def kill():
                process.kill()
                process.wait()

            await loop.run_in_executor(None, kill)
            raise


def del_file(path):
    if not os.path.exists(path): return
    ls = os.listdir(path)
    for i in ls:
        c_path = os.path.join(path, i)
        if os.path.isdir(c_path):
            if len(os.listdir(c_path)) > 0:
                try:
                    del_file(c_path)
                except Exception as e:
                    print(f"[WARNING]删除文件{c_path}遇到问题{traceback.format_exc()}")
            try:
                os.removedirs(c_path)
            except FileNotFoundError:
                pass
        else:
            os.remove(c_path)


async def simple_download(url):
    err = None
    for i in range(3):
        try:
            r = requests.get(url, timeout=(5, 30))
            with open(str(dist_pl.joinpath("libs", pathlib(url).name)), "wb") as code:
                code.write(r.content)
                return
        except Exception as e:
            print(f"重试下载({i+1})：{url}。遇到错误{e}")
            err = e
    raise Exception(f"重试3次，无法下载{url}, 最后一次失败原因：{err}")


"""
用异步处理来执行，因为这里有很多异步操作。
如果采用线程的话，比较庞大，理解起来不直观。
附带一个自己实现的asyncio.sleep()
@asyncio.coroutine
def mysleep(seconds):
    now = round(time.time()) # 启动这个协程的时间
    while round(time.time())-now<seconds:  # 此携程不会退出，直到当前系统时间减去启动时间达到参数指定的秒数
        yield # 不结束携程，采用yield交出CPU
    # 当while不成立，这里就return结束携程了，达到了sleep的效果s
"""
async def run():
    del_file(str(dist_pl.resolve()))

    console = None
    tasks = []
    try:
        """
        构造一个用于发送命令和接收返回的嵌套事件协程，目标是在一个虚拟环境中执行pip install，并且搜集Downlaoding和Using cached的结果
        """
        xwin = "cmd" if is_win() else "/bin/bash"
        console = subprocess.Popen(xwin, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) #

        # 定义搜集器
        __default_collector = lambda res: print(f"get  [resp]: {res.strip()}")

        class __error_collector:
            err = []
            error_comming = False
            error_end = True
            def collect(self, resp):
                strip = resp.strip()
                if strip.lower().find("invalid")>=0 or strip.lower().find("error")>=0:
                    print(f"get [error]: {strip}")
                    self.err.append(resp)
                else:
                    # 收到异常信息
                    if strip.startswith("Traceback"):
                        self.error_comming = True
                    # 如果没有异常信息，直接结束
                    if not self.error_comming:
                        return
                    print(f"get [error]: {strip}")
                    # 当error_comming开关打开后，采集异常
                    self.err.append(resp)
                    # 采集结束，设置comming为false
                    if not resp.startswith(" "):
                        self.err.append(resp.strip()) # 最后一行一般是ERROR信息
                        self.error_comming = False


        # 定义处理方法
        async def process_command(cmd_list):
            for i in range(len(cmd_list)):
                cmd, command_over_signal, info_collecotr = cmd_list[i]["cmd"], cmd_list[i]["confirm"], cmd_list[i].get("info_collectors")
                console.stdin.write((cmd + ("\r\n" if is_win() else "\n")).encode())
                console.stdin.flush()
                # 发送特殊的echo，目的是给一个“结束”信号
                console.stdin.write((f"echo {command_over_signal}" + ("\r\n" if is_win() else "\n")).encode())
                console.stdin.flush()
                while True:
                    resp = await loop.run_in_executor(None, console.stdout.readline)
                    try:
                        resp = resp.decode("UTF-8")
                    except UnicodeDecodeError :
                        resp = resp.decode("GBK")
                    [collect(resp) for collect in info_collecotr]
                    if resp.strip() == command_over_signal:
                        # 说明上一个命令已经发送，并且执行成功
                        break

        # region: 打包
        print("执行打包....")
        commands = [
            # 执行setup.py
            {"cmd": f"cd {work_space}",  "confirm": "___cd_workspace_over", "info_collectors":  [__default_collector]},
            {"cmd": f"{py} setup.py bdist_wheel",  "confirm": "___setup_bdist_over", "info_collectors":  [__default_collector]}
        ]
        task = asyncio.ensure_future(process_command(commands))
        tasks.append(task)
        await asyncio.wait_for(task, timeout=None)
        print("执行打包完成.")
        # endregion

        # region: 复制requirements.txt
        print("复制requirements.txt到dist目录....")
        temp_requirements_txt = str(dist_pl.joinpath("temp_requirements.txt").resolve())
        with open(temp_requirements_txt, "w+") as output:
            with open(str(work_pl.joinpath("./requirements.txt").resolve()), "r") as read:
                for line in read.readlines():
                    output.write(line)
        print("复制requirements.txt到打包目录完成.")
        # endregion

        # region: 构建虚拟环境
        print("virtualenv..")
        out, err = await exec_shell([py, "-m", "virtualenv", str(dist_pl.joinpath("temp_env").resolve())])
        if err:
            raise Exception("执行构建虚拟环境错误", err)
        print("virtualenv....ok")
        # endregion

        # region: 进入虚拟环境，并安装twine
        task = asyncio.ensure_future(process_command([
            # 打开venv
            {"cmd": str(dist_pl.joinpath('temp_env', "Scripts", "activate").resolve()) if is_win() \
                else f"source {str(dist_pl.joinpath('temp_env', 'bin', 'activate').resolve())}", "confirm": "___open_venv_over",
             "info_collectors": [__default_collector]}
        ]))
        tasks.append(task)
        await asyncio.wait_for(task, timeout=None)
        error_collector = __error_collector()
        # 安装twine
        task = asyncio.ensure_future(process_command([
            # 执行pip install twine
            {"cmd": f"python -m pip install -i {pip_source} twine", "confirm": "___install_twine_over", "info_collectors":  [__default_collector]},
        ]))
        tasks.append(task)
        await asyncio.wait_for(task, timeout=None)
        if len(error_collector.err)>0:
            raise Exception(f"虚拟环境安装twine出现异常：{error_collector.err}")
        # 检查twine
        twineok = []
        __checktwine_collector = lambda resp: twineok.append(resp) if resp.strip().startswith("twine") else None
        task = asyncio.ensure_future(process_command([
            # 执行pip install twine
            {"cmd": f"python -m pip list", "confirm": "___pip_list_twine_over", "info_collectors":  [__default_collector, __checktwine_collector]},
        ]))
        tasks.append(task)
        await asyncio.wait_for(task, timeout=None)
        if len(twineok)==0:
            raise Exception(f"虚拟环境没有正确安装twine，请检查控制台输出")
        # endregion

        # region: 执行python -m pip wheel -r requirements.txt -w ./dist/libs -b ./dist/libs_build
        error_collector = __error_collector()
        task = asyncio.ensure_future(process_command([
            {"cmd": f"python -m pip wheel -r {temp_requirements_txt} -i {pip_source} -w {libs_dir} -b {libs_build_dir}",
             "confirm": "___pip_wheel_over", "info_collectors":  [__default_collector, error_collector.collect]},
        ]))
        tasks.append(task)
        await asyncio.wait_for(task, timeout=None)
        if len(error_collector.err)>0:
            raise Exception(f"虚拟环境执行pip wheel出现异常：{error_collector.err}")
        # endregion

        # 把下载好的依赖和打包目标整理好
        wheels = [str(dist_pl.joinpath(whl).resolve()) for whl in os.listdir(str(dist_pl.resolve())) if whl.endswith(".whl")]
        wheels.extend([str(pathlib(libs_dir, whl).resolve()) for whl in os.listdir(libs_dir) if whl.endswith(".whl")])

        # 删除builds_lib
        del_file(libs_build_dir)

        # region 生成命令，twine upload
        access = ""
        if nexus_user and nexus_pwd:
            access = f"-u {nexus_user} -p {nexus_pwd}"
        if gen_file:
            with open(str(dist_pl.joinpath("upload.sh").resolve()), "a+") as code:
                # 把下载好的依赖加入上传列表
                for whl in wheels:
                    cmd = f"python -m twine upload --repository-url {nexus} {access} {whl}"
                    code.write(cmd+"\n")
        else:
            #  目前直接覆盖上传，按道理，应该查询一下，然后再上传
            print("上传到nexus....")
            for whl in wheels:
                for i in range(2):
                    try:
                        # 把任务启动起来
                        error_collector = __error_collector()
                        task = asyncio.ensure_future(process_command([
                            {"cmd": f"python -m twine upload --repository-url {nexus} {access} {whl} --disable-progress-bar",
                             "confirm": "___twine_upload_over", "info_collectors": [__default_collector, error_collector.collect]},
                        ]))
                        tasks.append(task)
                        await asyncio.wait_for(task, timeout=None)
                        if len(error_collector.err)>0:
                            raise Exception(f"虚拟环境执行twine upload出现异常：{error_collector.err}")
                        break
                    except Exception as e:
                        if i<1:
                            print(f"重试上传({i+1})：{whl}。 遇到错误：{e}")
                            continue
                        err = e
                    raise Exception(f"重试2次，无法上传{whl}到{nexus}, 最后一次失败原因：{err}")
            print("上传到nexus完成.")
        # endregion
    except Exception as e:
        print("出现错误：", e)
        print(traceback.format_exc())
    finally:
        import signal
        try:
            for task in tasks:
                try:
                    console.send_signal(signal.CTRL_C_EVENT if is_win() else signal.SIGKILL)
                except Exception as e:
                    print(f"[WARING]关闭虚拟控制台遇到问题，{e}")
                # while not task.cancelled() and not task.done():
                #     print(f"task.cancelled {task.cancelled()}, task.done {task.done()}")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    print(f"{task} is cancelled now")
        except Exception as e:
            print(f"[WARING]停止task遇到问题，{e}")

        try:
            print("关闭虚拟控制台....")
            console.kill()
            os.kill(console.pid, 9 if is_win() else signal.SIGKILL)
            os.killpg(os.getpgid(console.pid), 9 if is_win() else signal.SIGKILL)
        except Exception as e:
            pass

        loop.stop()

pip_source = "https://mirrors.aliyun.com/pypi/simple/"
nexus = None
keepwhl = True  # 是否保留生成的依赖文件
nexus_user = None
nexus_pwd = None
gen_file = False  # 以生成sh文件的方式写入twine命令，而不是直接执行上传
work_space = None  # 打包的目标目录
upload_timeout = 60 # twine上传的超时时间
py = "python"
work_pl = None
dist_pl = None
if __name__ == '__main__':
    opts, args = getopt.getopt(sys.argv[1:],
                               "i:w:t:k:u:p:f",
                               ["source=", "project-dir=", "nexus=", "keep-whl=","username=","password=","python-bin=","upload-timeout="])
    #
    for arg, val in opts:
        if arg in ("-i","--source"):
            pip_source = val
        if arg in ("-t", "--nexus"):
            nexus = val
            if not nexus.endswith("/"):
                nexus = nexus+"/"
        if arg in ("-k", "--keep-whl"):
            keepwhl = val=="True"
        if arg in ("-u", "--username"):
            nexus_user = val
        if arg in ("-p", "--password"):
            nexus_pwd = val
        if arg == "-w":
            work_space = val
        if arg == "-f":
            gen_file = True
        if arg == "--python-bin":
            if not os.path.exists(val):
                raise Exception(f"{val} 不存在")
            py = val
        if arg == "--upload-timeout":
            upload_timeout = int(val)

    if not work_space:
        raise Exception(f"-w [workspace] must specified")
    if not os.path.exists(work_space):
        raise Exception(f"指定的项目{work_space} 不存在")
    work_pl = pathlib(work_space)
    dist_pl = work_pl.joinpath("dist") # setup.py的目标目录
    if not os.path.exists(str(work_pl.joinpath("setup.py").resolve())):
        raise Exception(f"项目{arg} 中没有setup.py")
    libs_dir = str(dist_pl.joinpath('libs').resolve())
    libs_build_dir = str(dist_pl.joinpath('libs_build').resolve())

    if nexus is None:
        raise Exception("-t [nexus url] must specified")

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run())
        loop.close()
    except Exception as e:
        print(f"[CATCHING INFO]{e}")
    finally:
        loop.stop()
        if not keepwhl:
            import time
            for i in range(3):
                try:
                    print("清除dist....")
                    del_file(str(dist_pl.resolve()))
                except Exception as e:
                    print(f"[WARING]重试（{i+1}）删除{str(dist_pl.resolve())}遇到问题，{e}")
                    time.sleep(3)

        print("所有处理完成.")
        sys.exit(0)

