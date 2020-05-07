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


def is_win():
    import sys
    return sys.platform == "win32"


py = "python"

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


async def del_file(path):
    if not os.path.exists(path): return
    ls = os.listdir(path)
    for i in ls:
        c_path = os.path.join(path, i)
        if os.path.isdir(c_path):
            if len(os.listdir(path)) > 0:
                await del_file(c_path)
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
            r = requests.get(url)
            with open(str(pathlib("./dist", "libs", pathlib(url).name)), "wb") as code:
                code.write(r.content)
                return
        except Exception as e:
            print("重试下载："+url)
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
    print("[[[WARNING!!!!!]]]因为采用了virtualenv，所以不要使用IDE的运行按钮执行，否则将导致不可预料的问题！！！")
    pip_source = "https://pypi.tuna.tsinghua.edu.cn/simple"
    nexus = None
    keepwhl = True  # 是否保留生成的依赖文件
    nexus_user = None
    nexus_pwd = None
    gen_file = False  # 以生成sh文件的方式写入twine命令，而不是直接执行上传
    work_space = None  # 打包的目标目录
    #
    opts, args = getopt.getopt(sys.argv[1:], "i:w:t:k:u:p:f", ["source=", "project-dir=", "nexus=", "keep-whl=","username=","password=","python-bin="])
    for arg, val in opts:
        if arg in ("-i","--source"):
            pip_source = val
        if arg in ("-t", "--nexus"):
            nexus = val
        if arg in ("-k", "--keep-whl"):
            keepwhl = val=="True"
        if arg in ("-u", "--username"):
            nexus_user = val
        if arg in ("-p", "--password"):
            nexus_pwd = val
        if arg == "-w":
            work_space = arg
        if arg == "-f":
            gen_file = True
        if arg == "--python-bin":
            if not os.path.exists(arg):
                raise Exception(f"{arg} 不存在")
            global py
            py = arg

    if not work_space:
        raise Exception(f"-w [workspace] must specified")
    if not os.path.exists(work_space):
        raise Exception(f"指定的项目{arg} 不存在")
    work_pl = pathlib(work_space)
    dist_pl = work_pl.joinpath("dist") # setup.py的目标目录
    if not os.path.exists(str(work_pl.joinpath("setup.py").resolve())):
        raise Exception(f"项目{arg} 中没有setup.py")

    if nexus is None:
        raise Exception("-t [nexus url] must specified")

    await del_file(str(pathlib("./dist").resolve()))

    try:
        # region: 打包
        print("执行打包....")
        out, err = await exec_shell(f"{py} {str(work_pl.joinpath('setup.py').resolve())} bdist_wheel")
        print(out)
        if err:
            raise Exception("执行打包出现错误", err)
        print("执行打包完成.")
        # endregion

        # region: 复制requirements.txt
        print("复制requirements.txt到dist目录....")
        temp_requirements_txt = str(dist_pl.joinpath("temp_requirements.txt").resolve())
        with open(temp_requirements_txt, "w+") as output:
            with open(str(pathlib("./requirements.txt").resolve()), "r") as read:
                for line in read.readlines():
                    output.write(line)
        print("复制requirements.txt到打包目录完成.")
        # endregion

        # region: 构建虚拟环境
        print("构建虚拟环境并执行pip install....")
        print("virtualenv..")
        out, err = await exec_shell([py, "-m", "virtualenv", str(dist_pl.joinpath("temp_env").resolve())])
        if err:
            raise Exception("执行构建虚拟环境错误", err)
        print("virtualenv....ok")
        xwin = "cmd" if is_win() else "/bin/bash"
        console = subprocess.Popen(xwin, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # endregion

        """
        构造一个用于发送命令和接收返回的嵌套事件协程，目标是在一个虚拟环境中执行pip install，并且搜集Downlaoding和Using cached的结果
        """
        # 定义搜集器
        __default_collector = lambda res: print(f"get resp: {res.strip()}")
        # 定义处理方法
        async def process_command(cmd_list):
            for i in range(len(cmd_list)):
                cmd, command_over_signal, info_collecotr = cmd_list[i]["cmd"], cmd_list[i]["confirm"], cmd_list[i].get("info_collectors")
                console.stdin.write((cmd + ("\r\n" if is_win() else "\n")).encode())
                console.stdin.flush()
                print(f"{cmd}  已发送")
                # 发送特殊的echo，目的是给一个“结束”信号
                console.stdin.write((f"echo {command_over_signal}" + ("\r\n" if is_win() else "\n")).encode())
                console.stdin.flush()
                while True:
                    resp = console.stdout.readline()
                    try:
                        resp = resp.decode("UTF-8")
                    except UnicodeDecodeError :
                        resp = resp.decode("GBK")
                    [collect(resp) for collect in info_collecotr]
                    if resp.strip() == command_over_signal:
                        # 说明上一个命令已经发送，并且执行成功
                        break

        # region: 使用process_command在虚拟环境中执行pip
        """ 
        构造process_command任务的参数
        """
        wheels = [] # 用于缓存结果
        # 定义搜集器
        def __pip_collector_(res):
            res = res.strip()
            if res.find("Downloading")==0:
                wheels.append(res.split(" ")[1])
            if res.find("Using cached")==0:
                wheels.append(res.split(" ")[2])
        #  定义一组命令，以及命令返回的采集器
        commands = [
            # 打开venv
            {"cmd": str(dist_pl.joinpath('temp_env', "Scripts", "activate").resolve()) if is_win() \
                else f"source {str(dist_pl.joinpath('temp_env', 'bin', 'activate').resolve())}", "confirm": "___onpen_venv_cover",
             "info_collectors": [__default_collector]},
            # 执行pip install -r
            {"cmd": f"pip install -i {pip_source} twine", "confirm": "___pip_install_twine_over",
             "info_collectors":  [__default_collector]},
            # 执行pip install -r
            {"cmd": f"pip install -i {pip_source} -r {temp_requirements_txt}", "confirm": "___pip_install_over",
             "info_collectors":  [__default_collector, __pip_collector_]}
        ]
        # 把任务启动起来
        tasks = [asyncio.ensure_future(process_command(commands))]
        fetures, pendings = await asyncio.wait(tasks)
        for task in fetures:
            #  执行迭代，让任务在主事件循环中处理完
            pass
        print("构建虚拟环境并执行pip install完成.")
        # endregion

        # region 执行一次下载，把所有的wheel下载到libs目录
        # 经过上一步的pip处理，得到一个wheels的集合列表
        if wheels:
            path = str(dist_pl.joinpath("libs").resolve())
            if not os.path.exists(path): os.mkdir(path)
            print("通过搜集到的whl列表进行下载....")
            tasks = []
            [tasks.append(asyncio.ensure_future(simple_download(whl))) for whl in wheels]
            fetures, pendings = await asyncio.wait(tasks)
            for task in fetures: pass #  执行迭代，让任务在主事件循环中处理完
            print("通过搜集到的whl列表进行下载完成.")
        # endregion

        # region 生成命令，或者重新进入虚拟环境，在虚拟环境中执行twine upload
        access = ""
        if nexus_user and nexus_pwd:
            access = f"-u {nexus_user} -p {nexus_pwd}"
        if gen_file:
            with open(str(dist_pl.joinpath("upload.sh").resolve()), "a+") as code:
                # 把下载好的依赖加入上传列表
                for whl in wheels:
                    target = str(dist_pl.joinpath("libs", pathlib(whl).name).resolve())
                    cmd = f"{py} -m twine upload --repository-url {nexus} {access} {target}"
                    code.write(cmd+"\n")

                # 把打包文件加入上传列表
                cmd = f"{py} -m twine upload --repository-url {nexus} {access} {str(dist_pl.resolve())+os.path.sep+'*.whl'}"
                code.write(cmd+"\n")
        else:
            #  目前直接覆盖上传，按道理，应该查询一下，然后再上传
            print("上传到nexus....")

            # 把下载好的依赖在虚拟环境中执行twine上传
            for whl in wheels:
                target = str(dist_pl.joinpath("libs", pathlib(whl).name).resolve())
                cmd = [
                   {"cmd": f"python -m twine upload --repository-url {nexus} {access} {target}", "confirm": "___twine_upload_over", "info_collectors": [__default_collector]},
                ]
                for i in range(4):
                    try:
                        # 把任务启动起来
                        tasks = [asyncio.ensure_future(process_command(cmd))]
                        fetures, pendings = await asyncio.wait(tasks)
                        #  执行迭代，让任务在主事件循环中处理完
                        for task in fetures: pass
                        break
                    except Exception as e:
                        if i<3:
                            print("重试上传："+whl)
                            continue
                        err = e
                    raise Exception(f"重试3次，无法上传{whl}到{nexus}, 最后一次失败原因：{err}")

            # 把打包文件加入上传列表
            target = str(dist_pl.resolve())+os.path.sep+'*.whl'
            cmd = [
                {"cmd": f"python -m twine upload --repository-url {nexus} {access} {target}",
                 "confirm": "___twine_upload_over", "info_collectors": [__default_collector]},
            ]
            for i in range(4):
                try:
                    # 把任务启动起来
                    tasks = [asyncio.ensure_future(process_command(cmd))]
                    fetures, pendings = await asyncio.wait(tasks)
                    #  执行迭代，让任务在主事件循环中处理完
                    for task in fetures: pass
                    break
                except Exception as e:
                    if i<3:
                        print("重试上传："+target)
                        continue
                    err = e
                raise Exception(f"重试3次，无法上传{target}到{nexus}, 最后一次失败原因：{err}")
            print("上传到nexus完成.")
        # endregion
    except Exception as e:
        import traceback
        print("出现错误：", e)
        print(traceback.format_exc())
    finally:
        # 清除env
        await del_file(dist_pl.joinpath("temp_env").resolve())
        if not keepwhl:
            print("清除dist....")
            await del_file(str(dist_pl.resolve()))
        print("所有处理完成.")


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run())
    loop.close()

