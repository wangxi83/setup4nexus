# setup4nexus
simply upload your Python(Pypi) or JAVA(Maven)  packaged artifact like wheel or jar AND WITH ALL THERE DEPENDENCIES to your local nexus

**YES!I don't and I never want to BUILD AND PUBLISH this repo to PUBLIC Pypi.**
** ALL you need is download this code and try it yourself whatelse**

# Why
think everybody has a local nexus, every one want's to 
1. build your code to wheel or jar
2. upload the wheel or jar to your nexus
3. with all there dependencies (whl or jar)

# Use
## for python
1. must include a setup.py
2. copy `setup4nexus.py` to your project's root
3. run with `python setup4nexus.py -t [nexus repo path] -u [nexus username] -p [nexus password]`
4. use "-k True(default value)|Fasle" to keep wheels after package and upload

## for java
1. must use maven
2. copy `maven4nexus.py` to your target maven module's root  
3. run with `python maven4nexus.py -t [nexus repo path] -u [nexus username] -p [nexus password]`

# How
1. For python
   - build with setuptools
   - copy requirements.txt named temp_requirements.txt to the dist dir
   - make a virtualenv in the dist dir
   - activate the virtaulenv and pip install -r  temp_requirements.txt
   - gather the Downloading or Using Cache urls
   - download there urls to dist/libs
   - use twine to upload dist/libs/* and the builded result to nexus

2. For java
   - build with maven
   - copy pom.xml to the target dir
   - modify pom.xml add plugin "maven-dependency-plugin"
   - execute maven goal "dependecy:list" and "dependency:copy-dependencies"
   - gather "dependecy:list" items and find the copied dependencies jars, resovle them to "mvn deploy:deploy-file" goal
   - tather builded result and resolve to "mvn deploy:deploy-file" goal
   - execute these goals

# Need Improve
1. setup4nexus define a "simple_download" to download dependencies and "simple_twine2nexus" to upload them and builed wheel, it should be more robustness
2. setup4nexus did not tested on a real MAC or linux, so I don't think it will work well, because it will open a terminal(only tested cmd on WIN) with subprocess to work.
3. maven4nexus, just like the same....
4. maven4nexus, may be writed as a maven plugin.
