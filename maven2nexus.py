# __author__ = 'wangx'
# -*- coding: UTF-8 -*-
#
# 1、在java的project module中执行
# 2、创建一个临时目录，创建target和dependencies
# 3、复制当前的pom.xml，修改，增加jar-plugin和dependency plugin，并设置好输出目录
# 4、执行package和dependency:copy-dependencies
# 5、将打包后的jar和依赖的jar上传到指定的nexus
# 整体支持四个参数: pip源、nexus地址、是否保留打包好的内容、maven home
# opts, args = getopt.getopt(sys.argv[1:], "i:t:m:j:p:", ["source=", "nexus=","maven-home=","java-home=", "project-path="])
#
# 其中(-t， --nexus= ), (-p, --project-path)是必选参数
# (-i, --source)=${maven repo}
# (-m, --maven-home)=${maven home}
# (-j, --java-home)=${java home}

import xml.etree.ElementTree as ET
from pathlib import Path as pathlib
import os, asyncio, sys, getopt, requests, subprocess, re


def is_win():
    import sys
    return sys.platform == "win32"


async def exec_shell(command: []):
    """
    执行指令，并返回(out, err)
    :param command:
    :return:
    """
    print(command)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=not isinstance(command, list)) as process:
        try:
            out, err = await loop.run_in_executor(None, process.communicate)
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

async def simple_twine2nexus(nexus_repo, whl, username=None, password=None):
    access = ""
    if username and password:
        access = f"-u {username} -p {password}"
    cmd = f"python -m twine upload --repository-url {nexus_repo} {access} {whl}"
    for i in range(3):
        try:
            print("重试上传："+whl) if i>0 else ""
            out, err = await exec_shell(cmd)
            if err:
                print("重试上传："+whl)
            return
        except Exception as e:

            err = e
    raise Exception(f"重试3次，无法上传{whl}到{nexus_repo}, 最后一次失败原因：{err}")


# 复制和处理原始pom
def process_source_pom(source_pom, repo, work_dir):
    """
    复制和处理原始pom
    :param repo:
    :param source_pom:
    :param work_dir:
    :return:
    """
    # 首先解析pom
    namespaces = dict([node for _, node in ET.iterparse(source_pom, events=['start-ns'])])
    namespaces = dict([(key if key else "default", namespaces.get(key)) for key in namespaces])  # 把默认命名空间命个名

    nexus_poms = {}
    # region:一个递归处理module的方法。主要是要修改父module的路径（因为，需要复制一个子pom出来，修改其parent，这样，才能让子pom继承nexus_pom的插件信息）
    def process_module(pom, parent_pom=None):
        tree = ET.parse(pom)
        root = tree.getroot()
        # 针对传入的pom处理几个东西：
        # 1、判断package类型，如果是jar，则直接处理。如果是pom，则处理其module
        pkg_el = root.find(".//default:packaging", namespaces=namespaces)
        if not pkg_el or pkg_el.text=="pom" or pkg_el.text=="jar":
            cur_pom_modules = root.find(".//default:modules", namespaces=namespaces)
            if cur_pom_modules:
                for module_el in cur_pom_modules:
                    # 这里的意思是——如果module写的是pom的路径，则直接使用。如果写的是模块名（目录），则加上pom.xml。
                    # 由于maven pom重module一般都是相对路径，因此这里通过pathlib可以很方便的就得到了全路径
                    module_pom = str(pathlib(module_el.text, "" if module_el.text.find(".xml")>0 else "pom.xml").resolve())
                    # 从当前pom所在的路径（pom/../）作为基准，找到module_pom的真实路径，处理module_pom
                    process_module(str(pathlib(pom, "../", module_pom).resolve()), parent_pom=pom)
                    # 将当前pom的module，修改为后面拷贝出来的nexus_pom的相对路径
                    module_el.text = module_pom.replace("pom.xml", "nexus_pom.xml")

            if parent_pom:
                # 设置每一个module_pom的parent到当前pom（最原始的除外）
                parent_el = root.find("./default:parent", namespaces=namespaces)
                if parent_el is not None:
                    relativePath_el = parent_el.find("./default:relativePath", namespaces=namespaces)
                    if relativePath_el is None:
                        relativePath_el = ET.SubElement(parent_el, "relativePath")
                    # 获取父pom.xml相对于当前pom所在目录的相对目录（在mvn的pom中，realativePath是相对于pom所在的目录，而不是pom.xml本身的）
                    relativePath_el.text = os.path.relpath(parent_pom, str(pathlib(pom).parent.resolve()))

        else:
            raise Exception(f"无法处理{pom}的packaging类型：{pkg_el.text}")

        # 修改每一个pom的plugin、repositories
        build_el = root.find("./default:build", namespaces=namespaces)
        if build_el is None: build_el=ET.SubElement(root, "build")
        plugins_el = build_el.find("./default:plugins", namespaces=namespaces)
        if plugins_el is None: plugins_el=ET.SubElement(build_el, "plugins")
        # 2.1.构建maven-jar-plugin
        plugin_el = plugins_el.find("./default:plugin/[default:artifactId='maven-jar-plugin']", namespaces=namespaces)
        if plugin_el is None:
            plugin_el = ET.SubElement(plugins_el, "plugin")
            ET.SubElement(plugin_el, "artifactId").text = "maven-jar-plugin"
        configuration_el = plugin_el.find("./default:configuration", namespaces=namespaces)
        if configuration_el is None: configuration_el = ET.SubElement(plugin_el, "configuration")
        outputDirectory_el = configuration_el.find("./default:outputDirectory", namespaces=namespaces)
        if outputDirectory_el is None: outputDirectory_el = ET.SubElement(configuration_el, "outputDirectory")
        # 设置jar的output目录为work_dir
        outputDirectory_el.text = str(pathlib(work_dir, "builds").resolve())
        archive_el = configuration_el.find("./default:archive", namespaces=namespaces)
        # 设置pom.properties的输出位置，便于后面使用
        if archive_el is None: archive_el = ET.SubElement(configuration_el, "archive")
        pomPropertiesFile_el = archive_el.find("./default:pomPropertiesFile", namespaces=namespaces)
        if pomPropertiesFile_el is None: pomPropertiesFile_el = ET.SubElement(archive_el, "pomPropertiesFile")
        artifactId = root.find("./default:artifactId", namespaces=namespaces).text
        pomPropertiesFile_el.text = str(pathlib(work_dir, "builds", artifactId+".pom.properties").resolve())
        # 2.2.构建maven-dependency-plugin
        plugin_el = plugins_el.find("./default:plugin/[default:artifactId='maven-dependency-plugin']", namespaces=namespaces)
        if plugin_el is None:
            plugin_el = ET.SubElement(plugins_el, "plugin")
            ET.SubElement(plugin_el, "artifactId").text = "maven-dependency-plugin"
        version_el = plugin_el.find("./default:version", namespaces=namespaces)
        if version_el is None: version_el = ET.SubElement(plugin_el, "version")
        version_el.text = "2.9"
        configuration_el = plugin_el.find("./default:configuration", namespaces=namespaces)
        if configuration_el is None: configuration_el = ET.SubElement(plugin_el, "configuration")
        outputDirectory_el = configuration_el.find("./default:outputDirectory", namespaces=namespaces)
        if outputDirectory_el is None: outputDirectory_el = ET.SubElement(configuration_el, "outputDirectory")
        # 设置jar的output目录为work_dir
        outputDirectory_el.text = str(pathlib(work_dir, "dependencies").resolve())
        # 2.3.构建repositories
        if repo:
            repositories_el = root.find("./default:repositories", namespaces=namespaces)
            if repositories_el is None: repositories_el=ET.SubElement(root, "repositories")
            repository_el = repositories_el.find(f"./default:repository/[default:url='{repo}']", namespaces=namespaces)
            if repository_el is None:
                repository_el = ET.SubElement(repositories_el, "repository")
                ET.SubElement(repository_el, "id").text = "repo"
                ET.SubElement(repository_el, "name").text = "repo"
                ET.SubElement(repository_el, "layout").text = "default"
                ET.SubElement(repository_el, "url").text = repo

        # 3、将修改后的新pom输出到当前pom同级目录
        [ET.register_namespace("" if key=="default" else key, namespaces.get(key)) for key in namespaces]
        nexus_pom = str(pathlib(pom, "../", "nexus_pom.xml").resolve())
        tree.write(nexus_pom, encoding="utf-8")
        nexus_poms[pom] = nexus_pom
    # endregion

    try:
        process_module(source_pom)
        return nexus_poms
    except Exception:
        for key in nexus_poms:
            try:os.remove(nexus_poms.get(key))
            except:pass
        raise


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
    maven_repo = None
    nexus = None
    nexus_user = None
    nexus_pwd = None
    maven_home = None
    java_home = None
    pom_path = None
    mvn_setting = None
    mvn_local_repository = None
    keep_result = True
    opts, args = getopt.getopt(sys.argv[1:], "i:t:m:j:u:p:s:k:",
                               ["source=", "nexus=","maven-home=","java-home=", "pom-path=", "username=", "password=", "mvn-settings=", "mvn-local-repository=", "keep="])
    for arg, val in opts:
        if arg in ("-i","--source"):
            maven_repo = val
        if arg in ("-t", "--nexus"):
            nexus = val
        if arg in ("-u", "--username"):
            nexus_user = val
        if arg in ("-p", "--password"):
            nexus_pwd = val
        if arg in ("-m", "--maven-home"):
            maven_home = val
        if arg in ("-j", "--java-home"):
            java_home = val
        if arg in ("-s", "--pom-path"):
            pom_path = val
        if arg=="--mvn-settings":
            mvn_setting = val
        if arg=="--mvn-local-repository":
            mvn_local_repository = val
        if arg in ("-k","--keep"):
            keep_result = val=="True"
    if pom_path is None or nexus is None:
        raise Exception("-s [maven module's pom path], -t [nexus url] must specify")
    if pathlib(pom_path).name.find(".xml")<0:
        raise Exception("-s [maven module's pom path] must a full '.xml' path")

    if not os.path.exists(pom_path): raise Exception(f"{pom_path} not exist")

    if nexus_user and nexus_pwd:
        username = "admin"
        password = "admin123"
        from urllib import parse
        result = parse.urlparse(nexus)
        nexus = f"{result.scheme}://{username}:{password}@{result.netloc}{result.path}"

    nexus_poms = {}
    try:
        # 创建工作目录
        work_dir = str(pathlib(pom_path, "../nexus_out", "target").resolve())
        if os.path.exists(work_dir): await del_file(work_dir)
        else: os.makedirs(work_dir, exist_ok=True)

        # 检查maven、java
        java = str(pathlib(java_home, "java").resolve()) if java_home else "java"
        out, err = await exec_shell(f"{java} -version")
        if err and err.find("java version")<0:
            raise Exception(f"{java} is not a valid java")

        mvn = str(pathlib(maven_home, "mvn").resolve()) if maven_home else "mvn"
        out, err = await exec_shell(f"{mvn} -version")
        if err or out.find("Maven home:")<0:
            raise Exception(f"{mvn} is not a valid mvn")
        maven_home = out[out.find("Maven home:")+len("Maven home:"):out.find("\n", out.find("Maven home:"))].strip()
        maven_home = str(pathlib(maven_home).resolve())
        if not mvn_setting:
            mvn_setting = str(pathlib(maven_home, 'conf', 'settings.xml').resolve())
        if mvn_local_repository:
            mvn_local_repository = f"-Dmaven.repo.local={mvn_local_repository} "

        # 复制和处理pom
        print("在目标目录构建nexus_pom....")
        nexus_poms = process_source_pom(pom_path, repo=maven_repo, work_dir=work_dir)
        print("在目标目录构建nexus_pom完成.")
        print(nexus_poms)

        # 开始执行maven的动作
        mvn = f"{java} -Dmaven.multiModuleProjectDirectory={str(pathlib(pom_path, '../').resolve())} " \
                "-DarchetypeCatalog=internal -Dmaven.multiModuleProjectDirectory=$M2_HOME " \
                f"-Dmaven.home={maven_home} -Dclassworlds.conf={str(pathlib(maven_home, 'bin', 'm2.conf').resolve())} " \
                f"-Dfile.encoding=UTF-8 -classpath {str(pathlib(maven_home, 'boot', 'plexus-classworlds-2.5.2.jar').resolve())} " \
                f"org.codehaus.classworlds.Launcher --errors -s {mvn_setting} " \
                f"{mvn_local_repository} -DskipTests=true -f {nexus_poms.get(pom_path)} "

        mvn_package = f"{mvn} package dependency:copy-dependencies"
        mvn_clean = f"{mvn} clean dependency:list" # clean的时候，顺便搜集依赖jar的信息

        # region: 执行maven
        print("开打虚拟控制台....")
        xwin = "cmd" if is_win() else "gnome-terminal -e '/bin/bash'"
        console = subprocess.Popen(xwin, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("开打虚拟控制台完成.")
        # endregion

        """
        构造一个用于发送命令和接收返回的嵌套事件协程，目标是在一个虚拟环境中执行pip install，并且搜集Downlaoding和Using cached的结果
        """
        # 定义搜集器
        __default_collector = lambda res: print(f"get resp: {res}")
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
                    resp = resp.decode("GBK")
                    [collect(resp) for collect in info_collecotr]
                    if resp.strip() == command_over_signal:
                        # 说明上一个命令已经发送，并且执行成功
                        break

        # region: 使用process_command执行构造好的mvn命令
        print("执行mvn命令....")

        dependencies = []
        m = re.compile(r".*:.*:jar:.*:compile", re.I)
        def __artifactlist_collector__(res):
            res = res.replace("[INFO]", "").strip()
            if m.match(res) and res not in dependencies:
                dependencies.append(res)

        commands = [
            {"cmd": mvn_package, "confirm": "___mvn_package_over", "info_collectors":  [__default_collector]},
            {"cmd": mvn_clean, "confirm": "___mvn_clean_over", "info_collectors":  [__default_collector, __artifactlist_collector__]}
        ]
        # 把任务启动起来
        tasks = [asyncio.ensure_future(process_command(commands))]
        fetures, pendings = await asyncio.wait(tasks)
        for task in fetures:
            #  执行迭代，让任务在主事件循环中处理完
            pass
        print("执行mvn命令打包完成.")
        # endregion

        #  region: 为dependencies构造mvn deploy命令，同时，把target也包含进来
        #  由于前面采用maven-jar-plugin输出了pom.properties到builds目录，因此这里就可以使用
        mvn_deploys = []
        pl = pathlib(work_dir)
        files = os.listdir(str(pl.joinpath("builds").resolve()))
        for file in files:
            if file.find(".properties")>0:
                version, groupId, artifactId = None, None, None
                with open(str(pl.joinpath("builds", file).resolve()), "r") as prop:
                    line = prop.readline()
                    while line:
                        if line.find("version=")>0: version = line.replace("version=","")
                        if line.find("groupId=")>0: groupId = line.replace("groupId=","")
                        if line.find("artifactId=")>0: artifactId = line.replace("artifactId=","")
                        line = prop.readline()
                if version and groupId and artifactId:
                    mvn_deploys.append(f"mvn deploy:deploy-file -DgroupId={groupId} "
                                       f"-DartifactId={artifactId} -Dversion={version} "
                                       f"-DgeneratePom=true -Dpackaging=jar "
                                       f"-Durl={nexus} "
                                       f"-Dfile={str(pl.joinpath(artifactId+'-'+version+'.jar'))}")

        for item in dependencies:
            item = item.split(":")
            # org.springframework:spring-aop:jar:4.2.0.RELEASE:compile
            version, groupId, artifactId = item[3],item[0],item[1]
            command = (f"mvn deploy:deploy-file -DgroupId={groupId} "
                        f"-DartifactId={artifactId} -Dversion={version} "
                        f"-DgeneratePom=true -Dpackaging=jar "
                        f"-Durl={nexus} "
                        f"-Dfile={str(pl.joinpath('dependencies', artifactId+'-'+version+'.jar'))}")
            if command not in mvn_deploys:
                mvn_deploys.append(command)
        # endregion

        # region 重新进入虚拟环境，在虚拟环境中执行mvn deploy
        #  目前直接覆盖上传，按道理，应该查询一下，然后再上传
        print("上传到nexus....")
        for mvn_deploy in mvn_deploys:
            out, err = await exec_shell(mvn_deploy)
            if err:
                raise Exception(err)
        print("上传到nexus完成.")
        # endregion
    except Exception as e:
        import traceback
        print("出现错误：", e)
        print(traceback.format_exc())
    finally:
        if not keep_result:
            for key in nexus_poms:
                try:os.remove(nexus_poms.get(key))
                except:pass
            await del_file(work_dir)
        print("所有处理完成.")


# async def call():
#     print(1)
#     return 100
#
#
# async def test():
#     task = [asyncio.ensure_future(call())]
#     results = await asyncio.gather(*task)
#     for result in results:
#         print('Task ret: ', result)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run())
    loop.close()