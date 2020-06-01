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
import os, asyncio, sys, getopt, subprocess, re, shutil


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
            try:
                return out.decode("UTF-8"), err.decode("UTF-8")
            except UnicodeDecodeError:
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


def find_el(el, path, nskey, namespaces):
    temp = None
    if namespaces and len(namespaces) > 0:
        temp = el.find(path, namespaces=namespaces)
    if temp is None and nskey:
        temp = el.find(path.replace(f"{nskey}:", ""))
    return temp


# 复制和处理原始pom
def process_source_pom(source_pom, repo, work_dir):
    """
    复制和处理原始pom
    :param repo:
    :param source_pom:
    :param work_dir:
    :return:
    """

    nexus_poms = {}
    parent_poms = {}
    properties_map = {}

    # region:一个递归处理module的方法。主要是要修改父module的路径（因为，需要复制一个子pom出来，修改其parent，这样，才能让子pom继承nexus_pom的插件信息）
    def process_module(pom, parent_pom=None):
        print("processing " + pom)
        # 首先解析pom
        namespaces = dict([node for _, node in ET.iterparse(pom, events=['start-ns'])])
        namespaces = dict([(nskey if nskey else "default", namespaces.get(nskey)) for nskey in namespaces])  # 把默认命名空间命个名

        tree = ET.parse(pom)
        root = tree.getroot()
        # 针对传入的pom处理几个东西：
        # 1、判断package类型，如果是jar，则直接处理。如果是pom，则处理其module
        skip_deploy = False  # 用于指明maven-deploy-plugin是否设置了skip
        artifactId = ""  # 用于引用pom的artifactid
        pkg_el = find_el(root, ".//default:packaging", "default", namespaces)
        if not pkg_el or pkg_el.text == "pom" or pkg_el.text == "jar":
            # 获取关键信息以及maven-deploy-plugin
            plugin_el = find_el(root, "./default:build/default:plugins/default:plugin/[default:artifactId='maven-deploy-plugin']", "default", namespaces)
            if plugin_el is not None:
                skip_el = find_el(plugin_el, "./default:configuration/default:skip", "default", namespaces)
                skip_deploy = skip_el is not None and skip_el.text == "true"
            artifactId = find_el(root, "./default:artifactId", "default", namespaces).text
            version_el = find_el(root, "./default:version", "default", namespaces)
            if version_el is None:
                version_el = find_el(root, "./default:parent/default:version", "default", namespaces)
            version = version_el.text
            if version.startswith("${"):
                version = properties_map.get(version.replace("${","").replace("}",""))
            groupId_el = find_el(root, "./default:groupId", "default", namespaces)
            if groupId_el is None:
                groupId_el = find_el(root, "./default:parent/default:groupId", "default", namespaces)
            groupId = groupId_el.text

            if parent_pom:
                # 设置每一个module_pom的parent到当前pom（最原始的除外）
                parent_el = find_el(root, "./default:parent", "default", namespaces)
                if parent_el is not None:
                    relativePath_el = find_el(parent_el, "./default:relativePath", "default", namespaces)
                    if relativePath_el is None:
                        relativePath_el = ET.SubElement(parent_el, "relativePath")
                        # 获取父pom.xml相对于当前pom所在目录的相对目录（在mvn的pom中，realativePath是相对于pom所在的目录，而不是pom.xml本身的）
                        nexus_parent_pom = parent_pom.replace("pom.xml", "nexus_pom.xml")
                        relativePath_el.text = os.path.relpath(nexus_parent_pom, str(pathlib(pom).parent.resolve()))
                    else:
                        relativePath_el.text = relativePath_el.text.replace("pom.xml", "nexus_pom.xml")
            else:
                # 获取最外层的properties
                properties_el = find_el(root, "./default:properties", "default", namespaces)
                if properties_el is not None:
                    for child in properties_el:
                        tag = child.tag
                        if tag.startswith("{"):
                            tag = tag[tag.index("}")+1:len(tag)]
                        properties_map[tag] = child.text

                # 在最外层构建插件。其他的都可以继承
                build_el = find_el(root, "./default:build", "default", namespaces)
                if build_el is None: build_el = ET.SubElement(root, "build")
                plugins_el = find_el(build_el, "./default:plugins", "default", namespaces)
                if plugins_el is None: plugins_el = ET.SubElement(build_el, "plugins")
                # 2.1.构建maven-jar-plugin
                # plugin_el = find_el(plugins_el, "./default:plugin/[default:artifactId='maven-jar-plugin']", "default", namespaces)
                # if plugin_el is None:
                #     plugin_el = ET.SubElement(plugins_el, "plugin")
                #     ET.SubElement(plugin_el, "artifactId").text = "maven-jar-plugin"
                # configuration_el = find_el(plugin_el, "./default:configuration", "default", namespaces)
                # if configuration_el is None: configuration_el = ET.SubElement(plugin_el, "configuration")
                # outputDirectory_el = find_el(configuration_el, "./default:outputDirectory", "default", namespaces)
                # if outputDirectory_el is None: outputDirectory_el = ET.SubElement(configuration_el, "outputDirectory")
                # # 设置jar的output目录为work_dir
                # outputDirectory_el.text = str(pathlib(work_dir, "builds").resolve())
                # archive_el = find_el(configuration_el, "./default:archive", "default", namespaces)
                # # 设置pom.properties的输出位置，便于后面使用
                # if archive_el is None: archive_el = ET.SubElement(configuration_el, "archive")
                # pomPropertiesFile_el = find_el(archive_el, "./default:pomPropertiesFile", "default", namespaces)
                # if pomPropertiesFile_el is None: pomPropertiesFile_el = ET.SubElement(archive_el, "pomPropertiesFile")
                # addMavenDescriptor_el = find_el(archive_el, "./default:addMavenDescriptor", "default", namespaces)
                # if addMavenDescriptor_el is None: addMavenDescriptor_el = ET.SubElement(archive_el, "addMavenDescriptor")
                # addMavenDescriptor_el.text = 'true'
                # artifactid_el = find_el(root, "./default:artifactId", "default", namespaces)
                # if artifactid_el is None:
                #     raise Exception(f"无法处理{pom}，没有artifactId")
                # artifactId = artifactid_el.text
                # pomPropertiesFile_el.text = str(pathlib(work_dir, "builds", artifactId+".pom.properties").resolve())
                # # 在mac上回出现maven-jar-plugin无法读取这个properties文件的异常，很奇怪，明明是创建，为什么会先读取？？这里尝试主动先写一个
                # os.makedirs(str(pathlib(work_dir, "builds").resolve()), exist_ok=True)
                # with open(pomPropertiesFile_el.text, "w"):
                #     pass

                # 2.2.构建maven-resource-plugin用于拷贝pom
                plugin_el = find_el(plugins_el, "./default:plugin/[default:artifactId='maven-resources-plugin']", "default", namespaces)
                if plugin_el is None:
                    plugin_el = ET.SubElement(plugins_el, "plugin")
                    ET.SubElement(plugin_el, "artifactId").text = "maven-resources-plugin"
                executions_el = find_el(plugin_el, "./default:executions", "default", namespaces)
                if executions_el is None: executions_el = ET.SubElement(plugin_el, "executions")
                execution_el = ET.SubElement(executions_el, "execution") # 直接创建一个新的execution
                ET.SubElement(execution_el, "id").text = "copy-pom-nexus"
                ET.SubElement(execution_el, "phase").text = "package"
                ET.SubElement(ET.SubElement(execution_el, "goals"), "goal").text = "copy-resources"
                configuration_el = ET.SubElement(execution_el, "configuration")
                outputDirectory_el = ET.SubElement(configuration_el, "outputDirectory")
                outputDirectory_el.text = "${project.build.directory}"
                resources_el = ET.SubElement(configuration_el, "resources")
                resource_el = ET.SubElement(resources_el, "resource")
                directory_el = ET.SubElement(resource_el, "directory")
                directory_el.text = "${project.basedir}"
                includes_el = ET.SubElement(resource_el, "includes")
                include_el = ET.SubElement(includes_el, "include")
                include_el.text = "pom.xml"

                # 2.3.构建maven-dependency-plugin
                plugin_el = find_el(plugins_el, "./default:plugin/[default:artifactId='maven-dependency-plugin']", "default", namespaces)
                if plugin_el is None:
                    plugin_el = ET.SubElement(plugins_el, "plugin")
                    ET.SubElement(plugin_el, "artifactId").text = "maven-dependency-plugin"
                version_el = find_el(plugin_el, "./default:version", "default", namespaces)
                if version_el is None: version_el = ET.SubElement(plugin_el, "version")
                version_el.text = "2.9"
                configuration_el = find_el(plugin_el, "./default:configuration", "default", namespaces)
                if configuration_el is None: configuration_el = ET.SubElement(plugin_el, "configuration")
                outputDirectory_el = find_el(configuration_el, "./default:outputDirectory", "default", namespaces)
                if outputDirectory_el is None: outputDirectory_el = ET.SubElement(configuration_el, "outputDirectory")
                # 设置jar的output目录为work_dir
                outputDirectory_el.text = work_dir
                # 2.3.构建repositories
                if repo:
                    repositories_el = find_el(root, "./default:repositories", "default", namespaces)
                    if repositories_el is None: repositories_el = ET.SubElement(root, "repositories")
                    repository_el = find_el(repositories_el, f"./default:repository/[default:url='{repo}']", "default", namespaces)
                    if repository_el is None:
                        repository_el = ET.SubElement(repositories_el, "repository")
                        ET.SubElement(repository_el, "id").text = "repo"
                        ET.SubElement(repository_el, "name").text = "repo"
                        ET.SubElement(repository_el, "layout").text = "default"
                        ET.SubElement(repository_el, "url").text = repo
        else:
            raise Exception(f"无法处理{pom}的packaging类型：{pkg_el.text}")

        # 获取modules（packaging为pom类型）
        cur_pom_modules = find_el(root, ".//default:modules", "default", namespaces)
        if cur_pom_modules is not None:
            for module_el in cur_pom_modules:
                # 这里的意思是——如果module写的是pom的路径，则直接使用。如果写的是模块名（目录），则加上pom.xml。
                # 由于maven pom重module一般都是相对路径，因此这里通过pathlib可以很方便的就得到了全路径
                module_pom = os.path.join(module_el.text, "" if module_el.text.find(".xml") > 0 else "pom.xml")
                # 从当前pom所在的路径（pom/../）作为基准，找到module_pom的真实路径，处理module_pom
                process_module(str(pathlib(pom, "../", module_pom).resolve()), parent_pom=pom)
                # 把pom类型引用起来
                if parent_poms.get(artifactId) is None:
                    parent_poms[artifactId] = {"groupId": groupId, "artifactId": artifactId, "version": version, "pom": pom}
                # 将当前pom的module，修改为后面拷贝出来的nexus_pom的相对路径
                module_el.text = module_pom.replace("pom.xml", "nexus_pom.xml")

        # 3、将修改后的新pom输出到当前pom同级目录
        [ET.register_namespace("" if key == "default" else key, namespaces.get(key)) for key in namespaces]
        nexus_pom = str(pathlib(pom, "../", "nexus_pom.xml").resolve())
        tree.write(nexus_pom, encoding="utf-8")
        nexus_poms[artifactId] = {"nexus_pom": nexus_pom, "skipdeploy": skip_deploy, "parent_pom": parent_pom}

    # endregion

    try:
        process_module(source_pom)
        return nexus_poms, parent_poms
    except Exception:
        for key in nexus_poms:
            try:
                os.remove(nexus_poms.get(key).get("nexus_pom"))
            except:
                pass
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
    gen_file = False
    opts, args = getopt.getopt(sys.argv[1:], "i:t:m:j:u:p:s:k:f",
                               ["source=", "nexus=", "maven-home=", "java-home=", "pom-path=", "username=", "password=", "mvn-settings=", "mvn-local-repository=", "keep="])
    for arg, val in opts:
        if arg in ("-i", "--source"):
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
        if arg == "--mvn-settings":
            mvn_setting = val
        if arg == "--mvn-local-repository":
            mvn_local_repository = val
        if arg in ("-k", "--keep"):
            keep_result = val == "True"
        if arg == "-f":
            gen_file = True
    if pom_path is None or nexus is None:
        raise Exception("-s [maven module's pom path], -t [nexus url] must specify")
    if pathlib(pom_path).name.find(".xml") < 0:
        raise Exception("-s [maven module's pom path] must a full '.xml' path")

    if not os.path.exists(pom_path): raise Exception(f"{pom_path} not exist")

    if nexus_user and nexus_pwd and nexus:
        from urllib import parse
        result = parse.urlparse(nexus)
        nexus = f"{result.scheme}://{nexus_user}:{nexus_pwd}@{result.netloc}{result.path}"

    nexus_poms = {}
    # 创建工作目录
    work_dir = str(pathlib(pom_path, "../nexus_out", "target").resolve())
    try:
        if os.path.exists(work_dir):
            await del_file(work_dir)
        else:
            os.makedirs(work_dir, exist_ok=True)

        # 检查maven、java
        java = str(pathlib(java_home, "java").resolve()) if java_home else "java"
        out, err = await exec_shell(f"{java} -version")
        if err and err.find("version") < 0:
            raise Exception(f"{java} is not a valid java")

        mvn = str(pathlib(maven_home, "mvn").resolve()) if maven_home else "mvn"
        out, err = await exec_shell(f"{mvn} -version")
        if out.find("Maven home:") < 0 and err.find("Maven home:") < 0:
            raise Exception(f"{mvn} is not a valid mvn")
        maven_home = out[out.find("Maven home:") + len("Maven home:"):out.find("\n", out.find("Maven home:"))].strip()
        maven_home = str(pathlib(maven_home).resolve())
        if not mvn_setting:
            mvn_setting = str(pathlib(maven_home, 'conf', 'settings.xml').resolve())
        if mvn_local_repository:
            mvn_local_repository = f"-Dmaven.repo.local={mvn_local_repository} "

        # 复制和处理pom
        print("在目标目录构建nexus_pom....")
        nexus_poms, origin_parent_poms = process_source_pom(pom_path, repo=maven_repo, work_dir=work_dir)
        print("在目标目录构建nexus_pom完成.")
        print(nexus_poms)

        # 开始执行maven的动作
        files = os.listdir(str(pathlib(maven_home, 'boot').resolve()))
        exe_jar = str(pathlib(maven_home, 'boot', 'plexus-classworlds-2.6.0.jar').resolve())
        for file in files:
            if file.startswith("plexus-classworlds"):
                exe_jar = str(pathlib(maven_home, 'boot', file).resolve())

        mvn = f"{java} -Dmaven.multiModuleProjectDirectory={str(pathlib(pom_path, '../').resolve())} " \
              "-DarchetypeCatalog=internal -Dmaven.multiModuleProjectDirectory=$M2_HOME " \
              f"-Dmaven.home={maven_home} -Dclassworlds.conf={str(pathlib(maven_home, 'bin', 'm2.conf').resolve())} " \
              f"-Dfile.encoding=UTF-8 -classpath {exe_jar} " \
              f"org.codehaus.classworlds.Launcher --errors -s {mvn_setting} " \
              f"{mvn_local_repository if mvn_local_repository is not None else ''} -DskipTests=true -f {pom_path.replace('pom.xml', 'nexus_pom.xml')} "

        mvn_package = f"{mvn} package -Dmdep.copyPom=true dependency:copy-dependencies"
        mvn_dependency_list = f"{mvn} dependency:list"  # 搜集依赖jar的信息
        mvn_clean = f"{mvn} clean"

        # region: 打开控制台
        print("开打虚拟控制台....")
        xwin = "cmd" if is_win() else "/bin/bash"
        console = subprocess.Popen(xwin, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("开打虚拟控制台完成.")
        # endregion

        """
        构造一个用于发送命令和接收返回的嵌套事件协程，目标是在一个虚拟环境中执行pip install，并且搜集Downlaoding和Using cached的结果
        """
        # 定义搜集器
        __default_collector = lambda res: print(f"get resp: {res.strip()}")
        errors = []
        __err_collector = lambda res: errors.append(res.strip()) if res.strip().startswith("[ERROR]") else None

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
                    except UnicodeDecodeError:
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

        build_targets = []

        def __build_targets_collector__(res):
            res = res.replace("[INFO]", "").strip()
            if res.startswith("Building jar:"):
                res = res.replace("Building jar:", "").strip()
                if not res.endswith("-sources.jar") and res not in build_targets:
                    # \programing\WorkSpace\FoundationPlatform\SobeyHive-Flow\IMPL\target\sobeyhive-flow-impl-1.0.jar
                    build_targets.append(res)

        commands = [
            {"cmd": mvn_clean, "confirm": "___mvn_clean_over", "info_collectors": [__default_collector]},
            {"cmd": mvn_package, "confirm": "___mvn_package_over", "info_collectors": [__default_collector, __err_collector, __build_targets_collector__]},
            {"cmd": mvn_dependency_list, "confirm": "___mvn_dependencylist_over", "info_collectors": [__default_collector, __artifactlist_collector__]}
        ]
        # 把任务启动起来
        tasks = [asyncio.ensure_future(process_command(commands))]
        fetures, pendings = await asyncio.wait(tasks, return_when=asyncio.tasks.FIRST_EXCEPTION)
        for task in fetures:
            #  执行迭代，让任务在主事件循环中处理完
            pass
        print("执行mvn命令打包完成.")
        if len(errors) > 0:
            print("+++++++++++++++++++ERROR++++++++++++++++")
            for err in errors:
                print(f"+ {err}")
            print("++++++++++++++++++++++++++++++++++++++++")
            raise Exception("执行mvn打包出现错误，请检查控制台[ERROR]信息")
        # endregion

        #  region: 为dependencies构造mvn deploy命令，同时，把target也包含进来
        #  由于前面采用maven-jar-plugin输出了pom.properties到builds目录，因此这里就可以使用
        #    —— 这句话作废。因为新版本的jar-plugin在mac上没测试通过，其逻辑和官网描述完全不一致。pomPropertiesFile的设置完全变了逻辑
        #       并且，在mac上测试的时候，只要自定义了jar-plugin，就会编译不通过。
        #       因此构造nexux_pom的时候，不再构造jar-plugin，从package的信息中获取原始的target位置
        mvn_deploys = []
        # pl = pathlib(work_dir)
        # files = os.listdir(str(pl.joinpath("builds").resolve()))
        # for file in files:
        #     if file.find(".properties")>0:
        #         version, groupId, artifactId = None, None, None
        #         with open(str(pl.joinpath("builds", file).resolve()), "r") as prop:
        #             line = prop.readline()
        #             while line:
        #                 if line.find("version=")>0: version = line.replace("version=","")
        #                 if line.find("groupId=")>0: groupId = line.replace("groupId=","")
        #                 if line.find("artifactId=")>0: artifactId = line.replace("artifactId=","")
        #                 line = prop.readline()
        #         if version and groupId and artifactId:
        #             mvn_deploys.append(f"mvn deploy:deploy-file -DgroupId={groupId} "
        #                                f"-DartifactId={artifactId} -Dversion={version} "
        #                                f"-DgeneratePom=true -Dpackaging=jar "
        #                                f"-Durl={nexus} "
        #                                f"-Dfile={str(pl.joinpath(artifactId+'-'+version+'.jar'))}")
        work_path = pathlib(work_dir)
        for item in build_targets:
            target_dir = str(pathlib(item, "../").resolve())
            propertyfile = str(pathlib(target_dir, "maven-archiver", "pom.properties").resolve())
            if not os.path.exists(propertyfile):
                raise Exception(f"错误，{target_dir}没有生成pom.properties")
            version, groupId, artifactId = None, None, None
            with open(propertyfile, "r") as prop:
                line = prop.readline()
                while line:
                    if line.find("version=") >= 0: version = line.replace("version=", "").strip()
                    if line.find("groupId=") >= 0: groupId = line.replace("groupId=", "").strip()
                    if line.find("artifactId=") >= 0: artifactId = line.replace("artifactId=", "").strip()
                    line = prop.readline()
                if version and groupId and artifactId:
                    # 把mvn-jar-plugin打包的target，拷贝到dependencies里面（删除原来通过maven-dependency-plugin拷贝的，因为它拷贝的pom是nexus_pom）
                    if os.path.exists(str(work_path.joinpath(artifactId + '-' + version + '.jar'))):
                        os.remove(str(work_path.joinpath(artifactId + '-' + version + '.jar')))
                    if os.path.exists(str(work_path.joinpath(artifactId + '-' + version + '.pom'))):
                        os.remove(str(work_path.joinpath(artifactId + '-' + version + '.pom')))
                    if os.path.exists(item) and os.path.exists(str(pathlib(item, '../', 'pom.xml').resolve())):
                        shutil.copyfile(item, str(work_path.joinpath(artifactId + '-' + version + '.jar')))
                        shutil.copyfile(str(pathlib(item, '../', 'pom.xml').resolve()), str(work_path.joinpath(artifactId + '-' + version + '.pom')))
                    # 对于build的内容，看看是否是Skip
                    if nexus_poms.get(artifactId) and nexus_poms.get(artifactId).get("skipdeploy") is False:
                        command = (f"mvn deploy:deploy-file -DgroupId={groupId} "
                                   f"-DartifactId={artifactId} -Dversion={version} "
                                   f"-DgeneratePom=false -Dpackaging=jar "
                                   f"-Durl={nexus} "
                                   f"-Dfile={str(work_path.joinpath(artifactId+'-'+version+'.jar'))} "
                                   f"-DpomFile={str(work_path.joinpath(artifactId+'-'+version+'.pom'))} "
                                   f"-DretryFailedDeploymentCount=3")
                    else:
                        command = f"echo \"根据maven-deploy-plugin配置，skip-deploy: {artifactId+'-'+version+'.jar'}\""

                    if command not in mvn_deploys:
                        mvn_deploys.append(command)

        for item in dependencies:
            item = item.split(":")
            # org.springframework:spring-aop:jar:4.2.0.RELEASE:compile
            version, groupId, artifactId = item[3], item[0], item[1]
            command = (f"mvn deploy:deploy-file -DgroupId={groupId} "
                       f"-DartifactId={artifactId} -Dversion={version} "
                       f"-DgeneratePom=false -Dpackaging=jar "
                       f"-Durl={nexus} "
                       f"-Dfile={str(work_path.joinpath(artifactId+'-'+version+'.jar'))} "
                       f"-DpomFile={str(work_path.joinpath(artifactId+'-'+version+'.pom'))} "
                       f"-DretryFailedDeploymentCount=3")
            if command not in mvn_deploys:
                mvn_deploys.append(command)

        # 把依赖的packaging为pom类型的pom也上传
        for key in origin_parent_poms:
            # 拷贝原始pom到目标目录
            artifactId = origin_parent_poms.get(key).get('artifactId')
            version = origin_parent_poms.get(key).get('version')
            source = origin_parent_poms.get(key).get('pom')
            target = str(work_path.joinpath(artifactId+"-"+version+"."+pathlib(source).name))
            shutil.copyfile(source, target)
            command = (f"mvn deploy:deploy-file -DgroupId={origin_parent_poms.get(key).get('groupId')} "
                       f"-DartifactId={artifactId} -Dversion={version} "
                       f"-DgeneratePom=false -Dpackaging=pom "
                       f"-Durl={nexus} "
                       f"-Dfile={target} "
                       f"-DretryFailedDeploymentCount=3")
            if command not in mvn_deploys:
                mvn_deploys.append(command)
        # endregion

        # region 重新进入虚拟环境，在虚拟环境中执行mvn deploy
        #  目前直接覆盖上传，按道理，应该查询一下，然后再上传
        if gen_file:
            try:
                os.remove(str(pathlib(work_dir, "../", "upload.sh").resolve()))
            except:
                pass
            with open(str(pathlib(work_dir, "../", "upload.sh").resolve()), "w") as code:
                # code.write(f'for pom in {str(pathlib(work_dir, "*.pom"))};'+
                #            f' do mvn deploy:deploy-file -Durl={nexus}' +
                #            ' -Dfile="${pom%%.pom}.jar" -DgeneratePom=false -DpomFile="$pom"')
                for mvn in mvn_deploys:
                    code.write(mvn + "\n")
        else:
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
                try:
                    os.remove(nexus_poms.get(key).get("nexus_pom"))
                except:
                    pass
            if not gen_file: await del_file(work_dir)
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
