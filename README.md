# setup4nexus
simply upload your Python(Pypi) or JAVA(Maven)  packaged artifact like wheel or jar AND WITH ALL THERE DEPENDENCIES to your local nexus

**YES!I don't and I never want to BUILD AND PUBLISH this repo to PUBLIC Pypi.**

**ALL you need is download this code and try it yourself whatelse**

# Why
think everybody has a local nexus, every one want's to 
1. build your code to wheel or jar
2. upload the wheel or jar to your nexus
3. with all there dependencies (whl or jar)

# Use
## for python
1. your project must have a setup.py
2. run with `python setup4nexus.py -w [project_dir](must) -t [nexus repo path](must) -u [nexus username](opt) -p [nexus password](opt)`
3. use "--python-bin={path to python bin}" to offer a target python(>=3.6)
4. use "-k True(default value)|Fasle" to keep wheels after package and upload (opt)
5. use "-i [pypi source url]" to set a custom pypi source (opt)
6. use "-f" to generate a upload.sh file into the target dir instead of upload to nexus 

## for java
1. must use maven
3. run with `python maven4nexus.py -s [maven module's pom path](must) -t [nexus repo path](must) -u [nexus username](opt) -p [nexus password](opt)`
4. use "-k True(default value)|Fasle" to keep nexux_pom.xmls and jars and dependency jars after package and upload (opt)
5. use "-m [Maven Home]" to detect your maven home (opt)
6. use "-j [JAVA Home]" to detect your java home (opt)
7. use "-i [maven repo url]" to set a custom mave repo source (opt)
8. use "--mvn-settings=[maven setting.xml]", "--mvn-local-repository=[your local maven repository]" (both opt)
9. use "-f" to generate a upload.sh file into the target dir instead of upload to nexus 
10. if you want to clean up , switch to the [maven module's path] use "mvn clean"

***note that: only support &lt;packaging&gt;jar&lt;/packaging&gt; or &lt;packaging&gt;pom&lt;/packaging&gt;--and all sub-final-module is &lt;packaging&gt;jar&lt;/packaging&gt;***

# How
1. For python
   - build with setuptools
   - copy requirements.txt named temp_requirements.txt to the dist dir
   - make a virtualenv in the dist dir
   - activate the virtaulenv and pip wheel -r temp_requirements.txt
   - use twine to upload dist/libs/* and the builded result to nexus

2. For java
   - build with maven
   - copy and backup the poms
   - modify pom.xml add plugin "maven-dependency-plugin"
   - execute maven goal "package" , "dependecy:list" and "dependency:copy-dependencies"
   - gather "dependecy:list" items and find the copied dependencies jars, resovle them to "mvn deploy:deploy-file" goal
   - tather builded result and resolve to "mvn deploy:deploy-file" goal
   - execute these goals

# Need Improve
1. setup4nexus define a "simple_download" to download dependencies and "simple_twine2nexus" to upload them and builed wheel, it should be more robustness
2. maven4nexus, may be writed as a maven plugin.
3. no "CLI help info",because i'm a lazy boy
4. when meet some err(like network err), you will run it again, but now, every stage will work again, it don't check its result--build or mavn package will re-run, it need to be improved and every stages should be more robustness.
