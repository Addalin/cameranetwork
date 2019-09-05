CameraNetwork
=============

Code for running and analyzing the Camera Network

~~Latest version can be downloaded from [bitbucket](http://bitbucket.org/amitibo/CameraNetwork_git).~~

[Installation Instructions](docs/source/install.rst)

[Usage Instructions](docs/source/usage.rst)

Documentation
-------------

Documentation is provided using [sphinx](http://www.sphinx-doc.org/).
To compile the documentation:<br />
Make sure Sphinx is installed `pip install -U Sphinx`

Navigate to document folder `cd docs`,<br />
then generate source files - <br />
Windows:
 ```sh
     sphinx-apidoc -f -o source ..\CameraNetwork
 ```
 Linux:
  ```sh
    sphinx-apidoc -f -o source/ ../CameraNetwork
 ```
 Finally, Create html document `make html` <br />
 or, for PDF:
  ```sh
  sudo apt-get install texlive-latex-recommended texlive-fonts-recommended texlive-latex-extra latexmk texlive-luatex texlive-xetex
  ```
  then
```sh
 make latexpdf
```
To view the docs navigate to `/docs/build/latex/CameraNetwork.pdf` or run `/docs/build/html/index.html`

Author
------

Amit Aides

License
-------

Please see the [LICENSE](LICENSE.md) file for details on copying and usage.
